"""Official NYC address lookup and NWS hourly forecasts for HeatRx."""

import json
import re
from datetime import date, datetime, time, timedelta
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from google.cloud import bigquery

from .heat_agent import GRID, _query

NY = ZoneInfo("America/New_York")
GEOSEARCH = "https://geosearch.planninglabs.nyc/v2/search"
NWS = "https://api.weather.gov"
STREET_WORDS = {"st": "street", "ave": "avenue", "blvd": "boulevard", "rd": "road",
                "pl": "place", "dr": "drive", "pkwy": "parkway", "ct": "court", "ln": "lane"}


def _fetch(url):
    request = Request(url, headers={"Accept": "application/geo+json",
                                   "User-Agent": "HeatRx Brooklyn (github.com/michaelplomer/A4I2026-svl-6)"})
    with urlopen(request, timeout=7) as response:
        return json.loads(response.read(2_000_000))


def _street(text):
    return " ".join(STREET_WORDS.get(word, word) for word in re.findall(r"[a-z0-9]+", text.lower()))


def lookup_brooklyn_address(address: str) -> dict:
    """Resolve a numbered Brooklyn street address. Ask for clarification on ambiguous or intersection-only input."""
    source = "NYC Department of City Planning GeoSearch"
    if re.search(r"\b(Manhattan|Queens|Bronx|Staten Island)\b", address, re.I):
        return {"status": "outside_brooklyn", "source": source}
    match = re.fullmatch(r"([0-9][0-9A-Za-z-]*)\s+(.+)", address.strip())
    if not match or len(address) > 180:
        return {"status": "need_numbered_brooklyn_address_or_map_coordinates", "source": source}
    number, street = match.groups()
    street = re.split(r"\s*,|\s+Brooklyn\b", street, maxsplit=1, flags=re.I)[0]
    try:
        features = _fetch(f"{GEOSEARCH}?{urlencode({'text': f'{number} {street}, Brooklyn, NY', 'size': 8})}")["features"]
        matches = {}
        for feature in features:
            props = feature["properties"]
            if (props.get("borough") == "Brooklyn" and props.get("accuracy") == "point"
                    and str(props.get("housenumber", "")).lower() == number.lower()
                    and _street(props.get("street", "")) == _street(street)):
                longitude, latitude = feature["geometry"]["coordinates"]
                matches[(round(latitude, 5), round(longitude, 5))] = props["label"]
    except (URLError, TimeoutError, ValueError, KeyError):
        return {"status": "address_service_unavailable", "source": source}
    if len(matches) != 1:
        return {"status": "ambiguous_or_no_exact_brooklyn_match", "source": source}
    (latitude, longitude), label = next(iter(matches.items()))
    return {"address": label, "latitude": latitude, "longitude": longitude, "source": source}


def _fahrenheit(value, unit):
    if value is None:
        return None
    if unit in {"F", "wmoUnit:degF"}:
        return round(value, 1)
    if unit in {"C", "wmoUnit:degC"}:
        return round(value * 9 / 5 + 32, 1)
    return None


def _grid_value(series, target):
    for row in series.get("values", []):
        start_text, duration = row["validTime"].split("/")
        interval = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", duration)
        if not interval:
            continue
        days, hours, minutes, seconds = (int(field or 0) for field in interval.groups())
        start = datetime.fromisoformat(start_text)
        if start <= target < start + timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds):
            return _fahrenheit(row.get("value"), series.get("uom"))
    return None


def brooklyn_hourly_weather(latitude: float, longitude: float, local_date: str, local_hour: int) -> dict:
    """Get the official NWS temperature and feels-like forecast for a Brooklyn point and New York local hour.

    Args:
        latitude: Brooklyn latitude in decimal degrees.
        longitude: Brooklyn longitude in decimal degrees.
        local_date: Forecast date in YYYY-MM-DD format.
        local_hour: New York local hour, from 0 to 23.
    """
    now = datetime.now(NY)
    try:
        target = datetime.combine(date.fromisoformat(local_date), time(local_hour), NY)
    except ValueError:
        return {"status": "invalid_local_date_or_hour"}
    if not now.replace(minute=0, second=0, microsecond=0) <= target < now + timedelta(days=7):
        return {"status": "outside_hourly_forecast_window", "current_local_time": now.isoformat()}
    if not (40.5 <= latitude <= 40.8 and -74.1 <= longitude <= -73.8):
        return {"status": "outside_brooklyn"}
    coverage = _query(f"""
        SELECT 1 AS covered FROM `{GRID}.tract_exposure`
        WHERE borough = 'Brooklyn'
          AND ST_COVERS(tract_geom, ST_GEOGPOINT(@longitude, @latitude))
        LIMIT 1
    """, bigquery.ScalarQueryParameter("longitude", "FLOAT64", longitude),
        bigquery.ScalarQueryParameter("latitude", "FLOAT64", latitude))
    if not coverage:
        return {"status": "outside_brooklyn"}
    source = "NOAA National Weather Service"
    try:
        point = _fetch(f"{NWS}/points/{latitude:.4f},{longitude:.4f}")["properties"]
        office = point["gridId"]
        if not re.fullmatch(r"[A-Z]{3}", office):
            return {"status": "weather_service_unavailable", "source": source}
        grid_url = f"{NWS}/gridpoints/{office}/{int(point['gridX'])},{int(point['gridY'])}"
        hourly = _fetch(f"{grid_url}/forecast/hourly")["properties"]
        period = next((row for row in hourly["periods"]
                       if datetime.fromisoformat(row["startTime"]) <= target
                       < datetime.fromisoformat(row["endTime"])), None)
        if period is None:
            return {"status": "hour_not_in_published_forecast", "source": source}
        try:
            grid = _fetch(grid_url)["properties"]
            heat_index = _grid_value(grid.get("heatIndex", {}), target)
            apparent = _grid_value(grid.get("apparentTemperature", {}), target)
        except (URLError, TimeoutError, ValueError, KeyError):
            heat_index = apparent = None
        return {
            "forecast_time": target.isoformat(), "issued_at": hourly.get("updateTime"),
            "temperature_f": _fahrenheit(period["temperature"], period["temperatureUnit"]),
            "relative_humidity_pct": period.get("relativeHumidity", {}).get("value"),
            "nws_heat_index_f": heat_index, "nws_apparent_temperature_f": apparent,
            "summary": period.get("shortForecast"), "source": source,
            "hourly_source_url": f"{grid_url}/forecast/hourly", "apparent_source_url": grid_url,
            "limitation": "NWS grid forecast, not a street-level measurement. Never add the HeatRx campaign offset to an hourly feels-like value. Null means NWS did not supply that metric for the requested hour.",
        }
    except (URLError, TimeoutError, ValueError, KeyError):
        return {"status": "weather_service_unavailable", "source": source}
