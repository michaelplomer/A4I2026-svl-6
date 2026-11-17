"""Live public services: official Brooklyn address lookup and NWS hourly / feels-like weather.

Ported into the ADK agent from branch `heatrx-brooklyn-agent-review` (046b3d9,
portal/backend/weather.py, by xkuang), adapted to data_tools._query / a4i_grid. Nothing is stored.

  lookup_brooklyn_address   NYC Department of City Planning GeoSearch (no key)
  brooklyn_hourly_weather   NOAA NWS hourly forecast + grid heat index / apparent temperature

Rule carried over: never add the campaign offset to an hourly feels-like value.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from google.cloud import bigquery

from .data_tools import GRID, _query

NY = ZoneInfo("America/New_York")
GEOSEARCH = "https://geosearch.planninglabs.nyc/v2/search"
NWS = "https://api.weather.gov"
STREET_WORDS = {"st": "street", "ave": "avenue", "av": "avenue", "blvd": "boulevard", "rd": "road",
                "pl": "place", "dr": "drive", "pkwy": "parkway", "ct": "court", "ln": "lane"}


def _fetch(url: str) -> dict:
    req = Request(url, headers={"Accept": "application/geo+json",
                                "User-Agent": "HeatRx Brooklyn (github.com/michaelplomer/A4I2026-svl-6)"})
    with urlopen(req, timeout=7) as r:
        return json.loads(r.read(2_000_000))


def _street(text: str) -> str:
    return " ".join(STREET_WORDS.get(w, w) for w in re.findall(r"[a-z0-9]+", text.lower()))


def lookup_brooklyn_address(address: str) -> dict:
    """Resolve a numbered Brooklyn street address (e.g. '412 Nostrand Avenue') to coordinates with
    NYC's official GeoSearch. Returns latitude/longitude to pass to get_block. For an intersection
    or an ambiguous address, ask the planner for a nearby numbered address or map coordinates;
    never guess a location.

    Args:
        address: A numbered street address in Brooklyn.
    """
    source = "NYC Department of City Planning GeoSearch"
    if re.search(r"\b(Manhattan|Queens|Bronx|Staten Island)\b", address, re.I):
        return {"status": "outside_brooklyn", "source": source}
    m = re.fullmatch(r"([0-9][0-9A-Za-z-]*)\s+(.+)", address.strip())
    if not m or len(address) > 180:
        return {"status": "need_numbered_brooklyn_address_or_map_coordinates", "source": source}
    number, street = m.groups()
    street = re.split(r"\s*,|\s+Brooklyn\b", street, maxsplit=1, flags=re.I)[0]
    try:
        feats = _fetch(f"{GEOSEARCH}?{urlencode({'text': f'{number} {street}, Brooklyn, NY', 'size': 8})}")["features"]
        matches = {}
        for f in feats:
            p = f["properties"]
            if (p.get("borough") == "Brooklyn" and p.get("accuracy") == "point"
                    and str(p.get("housenumber", "")).lower() == number.lower()
                    and _street(p.get("street", "")) == _street(street)):
                lon, lat = f["geometry"]["coordinates"]
                matches[(round(lat, 5), round(lon, 5))] = p["label"]
    except (URLError, TimeoutError, ValueError, KeyError):
        return {"status": "address_service_unavailable", "source": source}
    if len(matches) != 1:
        return {"status": "ambiguous_or_no_exact_brooklyn_match", "source": source}
    (lat, lon), label = next(iter(matches.items()))
    return {"status": "ok", "address": label, "latitude": lat, "longitude": lon, "source": source}


def _fahrenheit(value, unit):
    if value is None:
        return None
    if unit in {"F", "wmoUnit:degF"}:
        return round(value, 1)
    if unit in {"C", "wmoUnit:degC"}:
        return round(value * 9 / 5 + 32, 1)
    return None


def _grid_value(series: dict, target: datetime):
    for row in series.get("values", []):
        start_text, duration = row["validTime"].split("/")
        iv = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", duration)
        if not iv:
            continue
        d, h, mi, s = (int(x or 0) for x in iv.groups())
        start = datetime.fromisoformat(start_text)
        if start <= target < start + timedelta(days=d, hours=h, minutes=mi, seconds=s):
            return _fahrenheit(row.get("value"), series.get("uom"))
    return None


def brooklyn_hourly_weather(latitude: float, longitude: float, local_date: str, local_hour: int) -> dict:
    """Official NWS hourly air temperature, humidity, heat index and apparent ('feels like')
    temperature for a Brooklyn point at a New York local hour within the next 7 days.
    Report these separately from the HeatRx offset; never add the offset to them.

    Args:
        latitude: Brooklyn latitude in decimal degrees.
        longitude: Brooklyn longitude in decimal degrees.
        local_date: Date in New York, YYYY-MM-DD (resolve 'Thursday' to the next such date).
        local_hour: New York local hour, 0-23 (3 pm = 15).
    """
    now = datetime.now(NY)
    try:
        target = datetime.combine(date.fromisoformat(local_date), time(int(local_hour)), NY)
    except ValueError:
        return {"status": "invalid_local_date_or_hour", "current_local_time": now.isoformat()}
    if not now.replace(minute=0, second=0, microsecond=0) <= target < now + timedelta(days=7):
        return {"status": "outside_hourly_forecast_window", "current_local_time": now.isoformat()}
    if not (40.5 <= latitude <= 40.8 and -74.1 <= longitude <= -73.8):
        return {"status": "outside_brooklyn"}
    covered = _query(f"""
        SELECT 1 AS covered FROM `{GRID}.tract_exposure`
        WHERE borough = 'Brooklyn' AND ST_COVERS(tract_geom, ST_GEOGPOINT(@lon, @lat)) LIMIT 1
    """, bigquery.ScalarQueryParameter("lon", "FLOAT64", longitude),
        bigquery.ScalarQueryParameter("lat", "FLOAT64", latitude))
    if not covered:
        return {"status": "outside_brooklyn"}
    source = "NOAA National Weather Service"
    try:
        pt = _fetch(f"{NWS}/points/{latitude:.4f},{longitude:.4f}")["properties"]
        office = pt["gridId"]
        if not re.fullmatch(r"[A-Z]{3}", office):
            return {"status": "weather_service_unavailable", "source": source}
        grid_url = f"{NWS}/gridpoints/{office}/{int(pt['gridX'])},{int(pt['gridY'])}"
        hourly = _fetch(f"{grid_url}/forecast/hourly")["properties"]
        period = next((p for p in hourly["periods"]
                       if datetime.fromisoformat(p["startTime"]) <= target < datetime.fromisoformat(p["endTime"])), None)
        if period is None:
            return {"status": "hour_not_in_published_forecast", "source": source}
        try:
            grid = _fetch(grid_url)["properties"]
            heat_index = _grid_value(grid.get("heatIndex", {}), target)
            apparent = _grid_value(grid.get("apparentTemperature", {}), target)
        except (URLError, TimeoutError, ValueError, KeyError):
            heat_index = apparent = None
        return {
            "status": "ok", "forecast_time": target.isoformat(), "issued_at": hourly.get("updateTime"),
            "temperature_f": _fahrenheit(period["temperature"], period["temperatureUnit"]),
            "relative_humidity_pct": (period.get("relativeHumidity") or {}).get("value"),
            "nws_heat_index_f": heat_index, "nws_apparent_temperature_f": apparent,
            "summary": period.get("shortForecast"), "source": source,
            "hourly_source_url": f"{grid_url}/forecast/hourly",
            "limitation": ("NWS grid forecast (~2.5 km), not a street-level measurement. Never add the "
                           "HeatRx campaign offset to an hourly feels-like value. Null = NWS did not publish it."),
        }
    except (URLError, TimeoutError, ValueError, KeyError):
        return {"status": "weather_service_unavailable", "source": source}


def now_in_new_york() -> dict:
    """Current date, weekday and time in New York. Call before resolving 'Thursday' or 'tomorrow'."""
    now = datetime.now(NY)
    return {"local_time": now.isoformat(timespec="minutes"), "weekday": now.strftime("%A"),
            "date": now.date().isoformat()}


LIVE_TOOLS = [now_in_new_york, lookup_brooklyn_address, brooklyn_hourly_weather]
