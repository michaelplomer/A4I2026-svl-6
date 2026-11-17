"""National Weather Service forecast for Brooklyn (api.weather.gov -- US Government work,
public domain, no API key).

The portal turns a forecast high into a per-block estimate:

    estimated afternoon high on a block = NWS daytime high + the block's heat offset

The offset was measured relative to the city average during one afternoon, so pairing it with
a *daytime high* is the right match. It is not an hourly forecast.

Two calls, both cached:
  1. /points/{lat},{lon}       -> which NWS forecast office and 2.5 km grid cell covers Brooklyn
  2. /gridpoints/.../forecast  -> 12-hour periods for 7 days; we keep the daytime ones
NWS asks API clients to send an identifying User-Agent; we send a generic one.
"""
from __future__ import annotations

import json
import time
import urllib.request

from . import config

USER_AGENT = "brooklyn-heat-portal/0.1 (A4I 2026 hackathon demo)"
CACHE_SECONDS = 30 * 60
_cache: dict = {"at": 0.0, "payload": None, "forecast_url": None}


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/geo+json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _forecast_url() -> tuple[str, dict]:
    if _cache["forecast_url"]:
        return _cache["forecast_url"]
    p = _get(f"https://api.weather.gov/points/{config.FORECAST_LAT},{config.FORECAST_LON}")["properties"]
    meta = {"office": p["gridId"], "grid_x": p["gridX"], "grid_y": p["gridY"],
            "place": p["relativeLocation"]["properties"]["city"], "time_zone": p["timeZone"]}
    _cache["forecast_url"] = (p["forecast"], meta)
    return _cache["forecast_url"]


def forecast() -> dict:
    """Daytime forecast periods for the next ~7 days, cached for 30 minutes."""
    now = time.time()
    if _cache["payload"] and now - _cache["at"] < CACHE_SECONDS:
        return _cache["payload"]

    url, meta = _forecast_url()
    props = _get(url)["properties"]
    days = [
        {
            "name": p["name"],                       # "Thursday", "This Afternoon"
            "date": p["startTime"][:10],
            "high_f": p["temperature"] if p["temperatureUnit"] == "F"
                      else round(p["temperature"] * 9 / 5 + 32),
            "short_forecast": p["shortForecast"],
            "detailed_forecast": p["detailedForecast"],
            "icon": p.get("icon"),
        }
        for p in props["periods"] if p["isDaytime"]
    ]
    payload = {
        "source": "National Weather Service, api.weather.gov (public domain)",
        "point": {"lat": config.FORECAST_LAT, "lon": config.FORECAST_LON},
        **meta,
        "generated_at": props.get("generatedAt"),
        "updated_at": props.get("updateTime"),
        "days": days,
    }
    _cache.update(at=now, payload=payload)
    return payload
