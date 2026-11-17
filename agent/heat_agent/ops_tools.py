"""Operations tools: the Today (dispatch) and Decade (replacement / planting) tracks, the
Placement Recommendation, live forecast and Street View checks.

Added on branch `Dhinakar` on top of data_tools.py (same tables, same conventions):
  * blocks are "cell_i,cell_j" strings; tables live in a4i_grid; every query goes through
    data_tools._query (parameterized, 256 MB / 20 s caps, read-only).
  * dispatch_plan / replacement_priority / rank_planting_sites remember their last list in
    ADK session state, so "truck 2 broke down" reports what changed and
    placement_recommendation can assemble the last list without the agent re-typing cells.
  * placement_recommendation calls ML.EXPLAIN_PREDICT on heat_offset_v1 for all listed
    blocks in ONE query, then splits contributions into physical drivers (trees, open space,
    housing density) and location context (nearby measured streets, water share).

Nothing here forecasts the effect of an intervention, and nothing is written to BigQuery.
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import urllib.parse
import urllib.request

from google.adk.tools import ToolContext
from google.cloud import bigquery

from .data_tools import GRID, PROJECT, _parse_cells, _query, _tract_geoid

TREES = os.environ.get("HEAT_TREES_TABLE", f"{PROJECT}.nyc_trees.tree_census_2015")
MAPS_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
VISION_MODEL = os.environ.get("HEAT_AGENT_VISION_MODEL", os.environ.get("HEAT_AGENT_MODEL", "gemini-2.5-flash"))
DISPATCH_BASE_F = 90.0
CONF_WEIGHT_SQL = "CASE p.confidence WHEN 'measured' THEN 1.0 WHEN 'near_data' THEN 0.9 ELSE 0.6 END"

# Model features (sql/model/01_model_input.sql) grouped for "why it's hot".
CONTEXT_FEATURES = ("nbr_offset_1km", "nbr_n", "tract_water_share")   # where the block sits
TREE_FEATURES = ("trees_100m", "tree_dbh_sum_100m")
BUILT_FEATURES = ("bg_housing_units_per_km2", "epa_")
OPEN_SPACE_FEATURES = ("dist_open_space_m",)

LIMITATIONS = [
    "Heat readings come from one summer afternoon (NOAA Heat Watch) on volunteer-driven routes.",
    "The model explains part of block-to-block variation (CV R² ≈ 0.32); about half its weight is "
    "'nearby measured streets', so blocks far from a route are close to unknown.",
    "Hot-day counts apply one afternoon's offset to station highs: estimates, and 7 years is too "
    "short for a trend.",
    "Residents are ACS 2018/2019 estimates spread over ~100 m blocks; aggregate only.",
    "Tree data is the 2015 street-tree census (not current canopy); verify on site.",
    "Model contributions are associations, not causes; no cooling effect is forecast.",
]


# ---------------------------------------------------------------------------- helpers
def _area_where(area: str) -> tuple[str, list]:
    """'district 36' | tract '369' / GEOID | neighbourhood name | '' (all Brooklyn)."""
    a = (area or "").strip()
    if not a or a.lower() in {"brooklyn", "all"}:
        return "TRUE", []
    district = re.fullmatch(r"(?:council\s+)?district\s*(\d{1,2})", a, re.I)
    if district:
        return "pl.council_district = @area", [bigquery.ScalarQueryParameter("area", "STRING", str(int(district.group(1))))]
    tract = _tract_geoid(a)
    if tract:
        return "p.tract_geo_id = @area", [bigquery.ScalarQueryParameter("area", "STRING", tract)]
    return "LOWER(pl.neighborhood) LIKE LOWER(@area)", [bigquery.ScalarQueryParameter("area", "STRING", f"%{a}%")]


def _dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def _spread(rows: list[dict], n: int, min_sep_m: float) -> list[dict]:
    """Greedy: keep the best rows that are at least min_sep_m apart (no two stops on one corner)."""
    out: list[dict] = []
    for r in rows:
        p = (r["lat"], r["lon"])
        if all(_dist_m(p, (q["lat"], q["lon"])) >= min_sep_m for q in out):
            out.append(r)
        if len(out) >= n:
            break
    return out


def _kmeans(points: list[tuple[float, float]], k: int, iters: int = 30) -> list[int]:
    if len(points) <= k:
        return list(range(len(points)))
    rnd = random.Random(7)
    centers = rnd.sample(points, k)
    labels = [0] * len(points)
    for _ in range(iters):
        labels = [min(range(k), key=lambda c: _dist_m(p, centers[c])) for p in points]
        for c in range(k):
            m = [p for p, l in zip(points, labels) if l == c]
            if m:
                centers[c] = (sum(x[0] for x in m) / len(m), sum(x[1] for x in m) / len(m))
    return labels


def _route(stops: list[dict]) -> list[dict]:
    """Nearest-neighbour order from the westernmost stop."""
    rest = sorted(stops, key=lambda r: r["lon"])
    out = [rest.pop(0)] if rest else []
    while rest:
        last = (out[-1]["lat"], out[-1]["lon"])
        nxt = min(rest, key=lambda r: _dist_m(last, (r["lat"], r["lon"])))
        rest.remove(nxt)
        out.append(nxt)
    return out


def _route_link(stops: list[dict]) -> str | None:
    pts = [f'{r["lat"]:.5f},{r["lon"]:.5f}' for r in stops[:11]]
    if not pts:
        return None
    url = f"https://www.google.com/maps/dir/?api=1&travelmode=driving&origin={pts[0]}&destination={pts[-1]}"
    return url + ("&waypoints=" + "|".join(pts[1:-1]) if len(pts) > 2 else "")


def _street_view(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={lat:.5f},{lon:.5f}"


def _remember(tool_context: ToolContext | None, key: str, cells: list[str]) -> list[str]:
    if tool_context is None:
        return []
    prev = list(tool_context.state.get(key) or [])
    tool_context.state[key] = cells
    tool_context.state["heatrx_last_list"] = key
    return prev


def _group(feature: str) -> str:
    f = feature.lower()
    if any(f.startswith(c) for c in CONTEXT_FEATURES):
        return "location_context"
    if any(f.startswith(c) for c in TREE_FEATURES):
        return "trees"
    if any(f.startswith(c) for c in OPEN_SPACE_FEATURES):
        return "open_space"
    if any(f.startswith(c) for c in BUILT_FEATURES):
        return "built_form"
    return "other"


def prescribe(contributions: list[dict], trees_100m: float | None, replaceable: int) -> dict:
    """Map a block's warming contributions + tree pits to a candidate action (never a promise)."""
    warming = [c for c in contributions if (c.get("contribution_f") or 0) > 0]
    physical = [c for c in warming if _group(c["feature"]) != "location_context"]
    context_f = sum(c["contribution_f"] for c in warming if _group(c["feature"]) == "location_context")
    physical_f = sum(c["contribution_f"] for c in physical)
    top = max(physical, key=lambda c: c["contribution_f"], default=None)
    if replaceable > 0:
        action = "Replace dead street trees / replant stumps (existing tree pits)"
    elif top and _group(top["feature"]) == "trees":
        action = "New street-tree planting (few trees nearby)"
    elif top and _group(top["feature"]) == "built_form":
        action = "Cool roofs / cool pavement candidate (dense housing)"
    elif top and _group(top["feature"]) == "open_space":
        action = "Shade / greening near the block (far from large open space)"
    elif (trees_100m or 0) == 0:
        action = "New street-tree planting candidate (no live street tree within 100 m)"
    else:
        action = "Cause unclear: prioritise by exposure (cooling access, outreach)"
    return {"action": action, "top_physical_driver": top, "physical_f": round(physical_f, 2),
            "location_context_f": round(context_f, 2), "context_dominates": context_f > physical_f}


# ---------------------------------------------------------------------------- ⚡ Today
def dispatch_plan(forecast_high_f: float, n_trucks: int = 4, stops_per_truck: int = 6, area: str = "",
                  include_predicted: bool = True, tool_context: ToolContext = None) -> dict:
    """Plan hydration-truck / wellness-check stops for a hot day, grouped into one zone per truck,
    with a Google Maps route per zone. Call again with fewer trucks to re-plan: the result says
    what changed.

    Ranking: (forecast high + block offset − 90°F, floored at 0) × (residents 65+ + households
    without a car) × confidence weight (measured 1.0, near_data 0.9). Extrapolated blocks are never
    used. Stops are at least 400 m apart.

    Args:
        forecast_high_f: Day's forecast high in °F (from get_forecast or the planner).
        n_trucks: Trucks or teams, 1-8.
        stops_per_truck: Stops per truck, 1-10.
        area: '' for all Brooklyn, a neighbourhood ('Brownsville'), tract ('369') or 'district 41'.
        include_predicted: Include model-predicted ('near_data') blocks, not only measured ones.
    """
    n_trucks, stops_per_truck = max(1, min(int(n_trucks), 8)), max(1, min(int(stops_per_truck), 10))
    where, params = _area_where(area)
    conf = "('measured','near_data')" if include_predicted else "('measured')"
    rows = _query(f"""
        SELECT p.cell_i, p.cell_j, ROUND(p.lat, 5) AS lat, ROUND(p.lon, 5) AS lon,
               pl.street_address, pl.neighborhood, pl.council_district,
               ROUND(p.offset_best_f, 2) AS offset_f, p.confidence,
               ROUND(@fc + p.offset_best_f, 1) AS est_block_high_f,
               ROUND(p.est_residents_65_plus) AS residents_65_plus,
               ROUND(p.est_households_no_car) AS households_no_car,
               GREATEST(@fc + p.offset_best_f - @base, 0)
                 * (IFNULL(p.est_residents_65_plus, 0) + IFNULL(p.est_households_no_car, 0))
                 * {CONF_WEIGHT_SQL} AS priority
        FROM `{GRID}.cell_profile_full` p
        LEFT JOIN `{GRID}.cell_places` pl USING (cell_i, cell_j)
        WHERE p.is_land AND p.borough = 'Brooklyn' AND p.confidence IN {conf} AND {where}
        ORDER BY priority DESC, p.cell_i, p.cell_j
        LIMIT 600
    """, *params, bigquery.ScalarQueryParameter("fc", "FLOAT64", forecast_high_f),
        bigquery.ScalarQueryParameter("base", "FLOAT64", DISPATCH_BASE_F))
    stops = [r for r in _spread(rows, n_trucks * stops_per_truck, 400) if (r["priority"] or 0) > 0]
    if not stops:
        return {"status": "no_blocks_above_threshold",
                "note": f"No block in '{area or 'Brooklyn'}' reaches {DISPATCH_BASE_F:.0f}°F at this forecast."}
    for r in stops:
        r["cell"] = f'{r["cell_i"]},{r["cell_j"]}'
        r["street_view"] = _street_view(r["lat"], r["lon"])
        r.pop("priority", None)
    labels = _kmeans([(r["lat"], r["lon"]) for r in stops], n_trucks)
    zones = []
    for z in sorted(set(labels)):
        route = _route([r for r, l in zip(stops, labels) if l == z])
        zones.append({"zone": len(zones) + 1, "stops": route, "route_link": _route_link(route),
                      "residents_65_plus": sum(r["residents_65_plus"] or 0 for r in route),
                      "households_no_car": sum(r["households_no_car"] or 0 for r in route)})
    cells = [r["cell"] for r in stops]
    prev = _remember(tool_context, "heatrx_last_dispatch", cells)
    if tool_context is not None:
        tool_context.state["heatrx_last_forecast_f"] = forecast_high_f
    return {
        "status": "ok", "forecast_high_f": forecast_high_f, "area": area or "Brooklyn",
        "zones": zones, "cells": cells,
        "what_changed": ({"added": [c for c in cells if c not in prev],
                          "dropped": [c for c in prev if c not in cells]} if prev else None),
        "limitations": ("Block high = forecast daytime high + one afternoon's offset: a planning "
                        "estimate, not an hourly forecast. Addresses are 'near' (closest 2015 street "
                        "tree). Field-check stops; this is not a verified dispatch history."),
    }


# ---------------------------------------------------------------------------- 🌳 Decade
def replacement_priority(area: str = "", n: int = 10, include_poor_health: bool = True,
                         tool_context: ToolContext = None) -> dict:
    """Dead street trees and stumps (existing tree pits) on hot, exposed Brooklyn blocks, with the
    exact coordinates and address of each pit from the 2015 street-tree census. Replanting an
    existing pit is the fastest, cheapest canopy gain. Ranked by estimated days ≥95°F × residents
    (65+ and no-car households) × confidence, on blocks hotter than average.

    Args:
        area: '' for all Brooklyn, a neighbourhood, tract ('369') or 'district 41'.
        n: Pits to return, 1-30.
        include_poor_health: Also list live trees in poor health (replacement candidates).
    """
    n = max(1, min(int(n), 30))
    where, params = _area_where(area)
    status = "t.status IN ('Dead','Stump')" + (" OR t.health = 'Poor'" if include_poor_health else "")
    rows = _query(f"""
        WITH pits AS (
          SELECT CAST(tree_id AS STRING) AS tree_id, status, health, spc_common AS species,
                 address, latitude AS lat, longitude AS lon,
                 CAST(FLOOR(latitude / 0.001) AS INT64) AS cell_i,
                 CAST(FLOOR(longitude / 0.001) AS INT64) AS cell_j
          FROM `{TREES}` t
          WHERE t.boroname = 'Brooklyn' AND ({status})
        )
        SELECT pits.*, pl.neighborhood, pl.council_district,
               ROUND(p.offset_best_f, 2) AS block_offset_f, p.confidence, p.trees_100m,
               ROUND(p.est_days_ge_95_per_year, 1) AS est_days_ge_95_per_year,
               ROUND(p.est_residents_65_plus) AS residents_65_plus,
               ROUND(p.est_households_no_car) AS households_no_car,
               (IFNULL(p.est_days_ge_95_per_year, 0) + 1)
                 * (IFNULL(p.est_residents_65_plus, 0) + IFNULL(p.est_households_no_car, 0) + 1)
                 * {CONF_WEIGHT_SQL}
                 * IF(pits.status IN ('Dead','Stump'), 1.0, 0.7) AS priority
        FROM pits
        JOIN `{GRID}.cell_profile_full` p USING (cell_i, cell_j)
        LEFT JOIN `{GRID}.cell_places` pl USING (cell_i, cell_j)
        WHERE p.is_land AND p.confidence IN ('measured','near_data') AND p.offset_best_f > 0 AND {where}
        ORDER BY priority DESC
        LIMIT 500
    """, *params)
    picked = _spread(rows, n, 60)
    for r in picked:
        r["cell"] = f'{r["cell_i"]},{r["cell_j"]}'
        r["street_view"] = _street_view(r["lat"], r["lon"])
        r.pop("priority", None)
    cells = list(dict.fromkeys(r["cell"] for r in picked))
    _remember(tool_context, "heatrx_last_decade", cells)
    return {"status": "ok" if picked else "no_pits_found", "pits": picked, "cells": cells,
            "limitations": ("2015 census status and location; confirm on site before scheduling. "
                            "Ranking targets heat + exposure; it does not estimate cooling from replanting.")}


def rank_planting_sites(area: str = "", n: int = 10, tool_context: ToolContext = None) -> dict:
    """Blocks for NEW street-tree planting: hotter than average, no live street tree within 100 m,
    many residents exposed. Ranked by estimated days ≥95°F × residents × confidence; blocks are
    at least 250 m apart.

    Args:
        area: '' for all Brooklyn, a neighbourhood, tract ('369') or 'district 41'.
        n: Blocks to return, 1-25.
    """
    n = max(1, min(int(n), 25))
    where, params = _area_where(area)
    rows = _query(f"""
        SELECT p.cell_i, p.cell_j, ROUND(p.lat, 5) AS lat, ROUND(p.lon, 5) AS lon,
               pl.street_address, pl.neighborhood, pl.council_district,
               ROUND(p.offset_best_f, 2) AS offset_f, p.confidence, p.trees_100m,
               ROUND(p.dist_open_space_m) AS dist_open_space_m,
               ROUND(p.est_days_ge_95_per_year, 1) AS est_days_ge_95_per_year,
               ROUND(p.est_residents) AS residents,
               ROUND(p.est_residents_65_plus) AS residents_65_plus,
               (IFNULL(p.est_days_ge_95_per_year, 0) + 1) * IFNULL(p.est_residents, 0) * {CONF_WEIGHT_SQL} AS priority
        FROM `{GRID}.cell_profile_full` p
        LEFT JOIN `{GRID}.cell_places` pl USING (cell_i, cell_j)
        WHERE p.is_land AND p.borough = 'Brooklyn' AND p.confidence IN ('measured','near_data')
          AND p.offset_best_f > 0 AND p.trees_100m = 0 AND {where}
        ORDER BY priority DESC
        LIMIT 400
    """, *params)
    picked = _spread(rows, n, 250)
    for r in picked:
        r["cell"] = f'{r["cell_i"]},{r["cell_j"]}'
        r.pop("priority", None)
    cells = [r["cell"] for r in picked]
    _remember(tool_context, "heatrx_last_decade", cells)
    return {"status": "ok" if picked else "no_blocks", "blocks": picked, "cells": cells,
            "limitations": ("Street-tree counts were a weak predictor of the offset in this data: these "
                            "are targeting candidates (heat + exposure + no trees), not a cooling forecast. "
                            "Sidewalk width, utilities and ownership need field checks.")}


# ---------------------------------------------------------------------------- 📄 deliverable
def placement_recommendation(track: str, cells: list[str] | None = None, scenario: str = "",
                             forecast_high_f: float | None = None,
                             tool_context: ToolContext = None) -> dict:
    """Assemble the Placement Recommendation for planners / council: ranked site cards (place,
    coordinates, Street View, heat offset + confidence, LIVE ML.EXPLAIN_PREDICT contributions split
    into physical drivers vs. location context, candidate action, hot days, who lives there, tree
    pits), plus limitations. Run equity_audit on the same cells and include its result.

    Args:
        track: 'today' or 'decade'.
        cells: Blocks as "cell_i,cell_j" in ranked order. Omit to use this session's last
            dispatch_plan (today) or replacement/planting list (decade).
        scenario: One line describing the situation, e.g. "Thursday 102°F, 4 trucks".
        forecast_high_f: Forecast high for the Today track (defaults to the last dispatch plan's).
    """
    state = tool_context.state if tool_context is not None else {}
    today = str(track).lower().startswith("t")
    if not cells:
        cells = list(state.get("heatrx_last_dispatch" if today else "heatrx_last_decade") or [])
    if forecast_high_f is None and today:
        forecast_high_f = state.get("heatrx_last_forecast_f")
    parsed = _parse_cells(cells or [])
    if not parsed:
        return {"status": "no_cells", "note": "Run dispatch_plan / replacement_priority / rank_planting_sites first, or pass cells."}
    keys = [f"{i},{j}" for i, j in parsed][:12]
    arr = bigquery.ArrayQueryParameter("cells", "STRING", keys)
    profiles = _query(f"""
        SELECT FORMAT('%d,%d', p.cell_i, p.cell_j) AS cell, ROUND(p.lat, 5) AS lat, ROUND(p.lon, 5) AS lon,
               pl.street_address, pl.neighborhood, pl.council_district,
               ROUND(p.offset_best_f, 2) AS offset_f, p.confidence, p.trees_100m,
               ROUND(p.est_days_ge_90_per_year, 1) AS d90, ROUND(p.est_days_ge_95_per_year, 1) AS d95,
               ROUND(p.est_days_ge_100_per_year, 1) AS d100,
               ROUND(p.est_residents) AS residents, ROUND(p.est_residents_65_plus) AS r65,
               ROUND(p.est_households_no_car) AS no_car, p.tract_poverty_rate AS poverty
        FROM `{GRID}.cell_profile_full` p
        LEFT JOIN `{GRID}.cell_places` pl USING (cell_i, cell_j)
        WHERE FORMAT('%d,%d', p.cell_i, p.cell_j) IN UNNEST(@cells)
    """, arr)
    explained = _query(f"""
        SELECT FORMAT('%d,%d', cell_i, cell_j) AS cell,
               ARRAY(SELECT AS STRUCT feature, ROUND(attribution, 3) AS contribution_f
                     FROM UNNEST(top_feature_attributions) ORDER BY ABS(attribution) DESC) AS contributions
        FROM ML.EXPLAIN_PREDICT(MODEL `{GRID}.heat_offset_v1`,
          (SELECT * FROM `{GRID}.cell_model_input` WHERE FORMAT('%d,%d', cell_i, cell_j) IN UNNEST(@cells)),
          STRUCT(6 AS top_k_features))
    """, arr)
    pits = _query(f"""
        SELECT FORMAT('%d,%d', CAST(FLOOR(latitude / 0.001) AS INT64), CAST(FLOOR(longitude / 0.001) AS INT64)) AS cell,
               COUNTIF(status = 'Dead') AS dead, COUNTIF(status = 'Stump') AS stumps
        FROM `{TREES}`
        WHERE boroname = 'Brooklyn' AND status IN ('Dead','Stump')
          AND FORMAT('%d,%d', CAST(FLOOR(latitude / 0.001) AS INT64), CAST(FLOOR(longitude / 0.001) AS INT64)) IN UNNEST(@cells)
        GROUP BY cell
    """, arr)
    by_prof = {r["cell"]: r for r in profiles}
    by_exp = {r["cell"]: r["contributions"] for r in explained}
    by_pit = {r["cell"]: r for r in pits}

    def fmt(cs, group_test):
        xs = [c for c in cs if group_test(_group(c["feature"]))][:3]
        return ", ".join(f'{c["contribution_f"]:+.2f}°F {c["feature"]}' for c in xs) or "none"

    cards, sites = [], []
    for rank, cell in enumerate(keys, 1):
        p = by_prof.get(cell)
        if not p:
            continue
        contrib = by_exp.get(cell, [])
        pit = by_pit.get(cell, {})
        rx = prescribe(contrib, p.get("trees_100m"), (pit.get("dead") or 0) + (pit.get("stumps") or 0))
        lines = [
            f"### #{rank} near {p.get('street_address') or 'block ' + cell}, {p.get('neighborhood') or ''}"
            f" · Council District {p.get('council_district') or '?'}",
            f"`{p['lat']:.5f}, {p['lon']:.5f}` · block {cell} · [Street View]({_street_view(p['lat'], p['lon'])})",
            f"- **Heat:** {(p.get('offset_f') or 0):+.2f}°F vs campaign average · confidence **{p.get('confidence')}**",
            f"- **Est. hot days/yr (2019–2025):** ≥90°F {p.get('d90')} · ≥95°F {p.get('d95')} · ≥100°F {p.get('d100')}",
        ]
        if today and forecast_high_f is not None and p.get("offset_f") is not None:
            lines.append(f"- **Forecast day:** {forecast_high_f:.0f}°F → ≈{forecast_high_f + p['offset_f']:.0f}°F block high (estimate)")
        lines += [
            f"- **Why (model, live):** physical: {fmt(contrib, lambda g: g != 'location_context')} · "
            f"location context: {fmt(contrib, lambda g: g == 'location_context')}",
            f"- **Candidate action:** {rx['action']}" + (" (location context dominates)" if rx["context_dominates"] else ""),
            f"- **Who:** ~{p.get('residents') or 0:.0f} residents · ~{p.get('r65') or 0:.0f} aged 65+ · "
            f"~{p.get('no_car') or 0:.0f} households without a car · tract poverty {p.get('poverty')}",
            f"- **Street trees:** {p.get('trees_100m')} live within 100 m · {pit.get('dead') or 0} dead · "
            f"{pit.get('stumps') or 0} stumps in block (2015 census)",
            "- **Next:** field-verify sidewalk, utilities, ownership, permits, maintenance",
        ]
        cards.append("\n".join(lines))
        sites.append({"cell": cell, "action": rx["action"], "contributions": contrib})

    md = "\n".join([
        f"# Placement Recommendation: {'Today' if today else 'Decade'} track",
        f"**Scenario:** {scenario or ('Brooklyn heat-day response' if today else 'Brooklyn long-term heat investment')}",
        "", "## Ranked sites", *cards, "",
        "## Equity check", "_Run equity_audit on these cells and paste the comparison here._", "",
        "## Limitations", *[f"- {x}" for x in LIMITATIONS], "",
        "_Sources: a4i_grid.heat_offset_v1 (BigQuery ML, ML.EXPLAIN_PREDICT) · NOAA Heat Watch · NOAA GHCN "
        "2019–2025 · ACS 2018/2019 · 2015 NYC street tree census. Candidates for further investigation, "
        "not approved sites._",
    ])
    if tool_context is not None:
        tool_context.state["heatrx_last_recommendation"] = md
    return {"status": "ok", "markdown": md, "cells": [s["cell"] for s in sites], "sites": sites}


# ---------------------------------------------------------------------------- live sources
def get_forecast() -> dict:
    """Official National Weather Service daytime highs for Brooklyn for the next days (live,
    api.weather.gov, public domain). Use the planner's number instead if they give one."""
    try:
        hdr = {"User-Agent": "heatrx-agent/0.1 (A4I 2026 hackathon)", "Accept": "application/geo+json"}
        req = urllib.request.Request("https://api.weather.gov/points/40.6501,-73.9496", headers=hdr)
        with urllib.request.urlopen(req, timeout=10) as r:
            fc_url = json.load(r)["properties"]["forecast"]
        with urllib.request.urlopen(urllib.request.Request(fc_url, headers=hdr), timeout=10) as r:
            periods = json.load(r)["properties"]["periods"]
        days = [{"name": p["name"], "date": p["startTime"][:10], "high_f": p["temperature"],
                 "short_forecast": p["shortForecast"]} for p in periods if p.get("isDaytime")][:5]
        return {"status": "ok", "days": days, "source": "NWS api.weather.gov (live)"}
    except Exception as e:  # network blocked, NWS down
        return {"status": "unavailable", "note": f"NWS unavailable ({type(e).__name__}); ask the planner for the forecast high."}


def inspect_site(lat: float, lon: float) -> dict:
    """Look at a site in Google Street View and have Gemini describe shade, surface, and whether there
    is an empty tree pit or sidewalk room for a tree. Imagery is fetched live and not stored.
    Needs GOOGLE_MAPS_API_KEY; otherwise returns a Street View link only.

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
    """
    link = _street_view(lat, lon)
    if not MAPS_KEY:
        return {"status": "not_configured", "street_view": link,
                "note": "Street View check needs GOOGLE_MAPS_API_KEY; share the link for a manual look."}
    loc = f"{lat:.6f},{lon:.6f}"
    meta_url = "https://maps.googleapis.com/maps/api/streetview/metadata?" + urllib.parse.urlencode({"location": loc, "key": MAPS_KEY})
    with urllib.request.urlopen(meta_url, timeout=10) as r:
        meta = json.load(r)
    if meta.get("status") != "OK":
        return {"status": "no_imagery", "street_view": link}
    from google import genai
    from google.genai import types

    parts = []
    for heading in (0, 180):
        img_url = "https://maps.googleapis.com/maps/api/streetview?" + urllib.parse.urlencode(
            {"size": "640x400", "location": loc, "fov": 100, "heading": heading, "key": MAPS_KEY})
        with urllib.request.urlopen(img_url, timeout=12) as r:
            parts.append(types.Part.from_bytes(data=r.read(), mime_type="image/jpeg"))
    prompt = ("Street-level heat planning check. Briefly report: (1) shade / tree cover, (2) surfaces "
              "(asphalt, concrete, roof colours), (3) empty tree pit or sidewalk room for a tree, "
              "(4) obstacles (utilities, driveways, bus stops). Say 'unclear' when unsure. Do not describe people.")
    client = genai.Client(vertexai=True, project=PROJECT, location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"))
    resp = client.models.generate_content(model=VISION_MODEL, contents=[*parts, prompt])
    return {"status": "ok", "observation": resp.text, "imagery_date": meta.get("date"), "street_view": link,
            "source": "Google Street View (live, not stored) + Gemini vision"}


OPS_TOOLS = [dispatch_plan, replacement_priority, rank_planting_sites, placement_recommendation,
             get_forecast, inspect_site]
