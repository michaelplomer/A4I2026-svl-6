"""Regular library hours and verified pedestrian path estimates for the demo shortlist."""

import re
from datetime import date, datetime, time
from html import unescape
from urllib.error import URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BPL = "https://www.bklynlibrary.org/locations"
CITY_FINDER = "https://finder.nyc.gov/coolingcenters/locations"
ROUTING = "https://routing.openstreetmap.de/routed-foot/table/v1/driving/"
ROUTE_COORDINATES = (
    "-73.9179374,40.675116;-73.9443029,40.6765714;-73.9092283,40.67965;"
    "-73.91516,40.684809;-73.934786,40.683011;-73.905206,40.664469"
)
ROUTE_SOURCE = f"{ROUTING}{ROUTE_COORDINATES}?sources=0;1;2&destinations=3;4;5&annotations=duration,distance"
TRACTS = ("36047030300", "36047031300", "36047036900")
BRANCHES = (
    ("saratoga", "Saratoga Library", (1112.0, 2450.6, 763.6)),
    ("macon", "Macon Library", (1748.2, 1127.6, 2013.6)),
    ("stone-avenue", "Stone Avenue Library", (1606.2, 3438.8, 1493.4)),
)
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
USER_AGENT = "HeatRx Brooklyn (github.com/michaelplomer/A4I2026-svl-6)"
VERIFIED_SCHEDULE_DATE = "2026-11-19"
VERIFIED_SCHEDULE_CHECKED_ON = "2026-11-17"
VERIFIED_SCHEDULE_SLUGS = frozenset({"saratoga", "macon", "stone-avenue"})


def _clean_text(html):
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def _regular_hours(slug, weekday):
    url = f"{BPL}/{slug}"
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=6) as response:
            page = response.read(1_000_000).decode("utf-8", errors="replace")
    except (URLError, TimeoutError):
        return {"status": "schedule_unavailable", "source_url": url}

    table = re.search(r'<table\b[^>]*class="[^"]*\bhours-table\b[^"]*"[^>]*>(.*?)</table>',
                      page, re.I | re.S)
    if not table:
        return {"status": "schedule_unavailable_check_branch_notice", "source_url": url}
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", table[1], re.I | re.S):
        cells = [_clean_text(cell) for cell in re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.I | re.S)]
        if len(cells) < 2 or cells[0].lower() != weekday.lower():
            continue
        times = re.findall(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\b", cells[1], re.I)
        if re.search(r'\bbranch-alert\b', page, re.I):
            return {"status": "branch_notice_check_manually", "published_regular_hours": cells[1],
                    "source_url": url}
        if len(times) == 2:
            closing_hour, closing_minute, suffix = times[-1]
            closes = time(int(closing_hour) % 12 + (12 if suffix.lower() == "p" else 0),
                          int(closing_minute or 0))
            return {"status": "regular_schedule_found", "published_regular_hours": cells[1],
                    "regular_closing_time_et": closes.isoformat(timespec="minutes"), "source_url": url}
        return {"status": "regularly_closed" if cells[1].lower() == "closed" else "schedule_unavailable",
                "published_regular_hours": cells[1], "source_url": url}
    return {"status": "schedule_unavailable", "source_url": url}


def brooklyn_branch_late_hours(local_date: str, target_close_hour: int = 21) -> dict:
    """Compare three Brooklyn branches for extending regular hours on a specified New York date.

    Args:
        local_date: Proposed date in YYYY-MM-DD format, in New York.
        target_close_hour: Proposed closing hour in New York, default 21 (9 p.m.).
    """
    try:
        day = date.fromisoformat(local_date)
        target = time(target_close_hour)
    except ValueError:
        return {"status": "invalid_local_date_or_hour"}
    now = datetime.now(ZoneInfo("America/New_York"))
    if day < now.date():
        return {"status": "past_date_regular_schedule_is_not_historical"}
    weekday = WEEKDAYS[day.weekday()]
    branches = []
    for slug, name, seconds in BRANCHES:
        hours = _regular_hours(slug, weekday)
        if (hours["status"] == "schedule_unavailable" and day.isoformat() == VERIFIED_SCHEDULE_DATE
                and slug in VERIFIED_SCHEDULE_SLUGS):
            hours = {**hours,
                "status": "dated_regular_schedule_snapshot",
                "published_regular_hours": "10 am - 8 pm",
                "regular_closing_time_et": "20:00",
                "snapshot_verified_on": VERIFIED_SCHEDULE_CHECKED_ON,
            }
        walks = {tract: round(duration / 60, 1) for tract, duration in zip(TRACTS, seconds)}
        reachable = [tract for tract, duration in zip(TRACTS, seconds) if duration <= 20 * 60]
        closing = hours.get("regular_closing_time_et")
        eligible = bool(closing and time.fromisoformat(closing) < target and reachable)
        branches.append({
            "branch": name, **hours, "walk_minutes_from_representative_tract_points": walks,
            "tract_points_within_20_minutes": reachable,
            "candidate_for_extension": eligible,
            "ranking_status": (
                "unable_to_rank" if not closing and hours["status"] != "regularly_closed"
                else "eligible" if eligible
                else "not_eligible_under_demo_criteria"
            ),
        })
    branches.sort(key=lambda branch: (
        not branch["candidate_for_extension"], branch["ranking_status"] == "unable_to_rank",
        -len(branch["tract_points_within_20_minutes"]),
        min(branch["walk_minutes_from_representative_tract_points"].values()),
    ))
    return {
        "ranking_status": ("unable_to_rank" if all(branch["ranking_status"] == "unable_to_rank" for branch in branches)
                           else "partial" if any(branch["ranking_status"] == "unable_to_rank" for branch in branches)
                           else "complete"),
        "planning_date": day.isoformat(), "weekday": weekday, "target_closing_time_et": target.isoformat(timespec="minutes"),
        "regular_schedules_checked_at": now.isoformat(), "branches": branches,
        "walking_source": "FOSSGIS OSRM pedestrian routing, © OpenStreetMap contributors",
        "walking_route_snapshot_date": "2026-11-17", "walking_route_evidence": ROUTE_SOURCE,
        "walking_map_corrections": "https://www.openstreetmap.org/fixthemap",
        "official_cooling_center_status": "Not verified for the proposed date. Check NYC Cool Options or 311.",
        "official_cooling_center_source": CITY_FINDER,
        "limitations": (
            "Three demo library candidates only. Walking estimates are from one representative point per "
            "tract, not household catchments or accessibility guarantees. Do not combine them with the "
            "existing nearest-facility walk estimates, which use a different routing source. Regular library "
            "hours (including dated snapshots) do not establish holiday hours, emergency cooling-center activation, or approval to stay "
            "open late. Confirm staffing, accessibility, exceptions and official activation before action."
        ),
    }
