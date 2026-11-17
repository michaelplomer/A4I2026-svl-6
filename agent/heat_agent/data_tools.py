"""Data tools: the agent's purpose-built reads from BigQuery ("tools you built").

Ported from the HeatRx review branch (6555d2f, portal/backend/heat_agent.py) and adapted:
  * get_block runs ML.EXPLAIN_PREDICT live on heat_offset_v1 (the agent CALLS the model)
    and adds place names (a4i_grid.cell_places).
  * shortlist_blocks can rank predicted blocks too, not only measured ones, and filters by
    tract, neighbourhood or council district.
  * equity_audit audits the blocks the agent actually recommended, not a fixed shortlist.
  * model_card reads the held-out score from a4i_grid.model_evaluation instead of hard-coding it.

Generic "query my tables" questions go through Google's managed BigQuery MCP server
(bigquery_mcp.py). Tools here are the ones with shape: a live model call, a ranked shortlist,
an audit with fixed definitions.

Every query is parameterized, capped at 256 MB billed and 20 s, and read-only.
Tables: sql/grid/README.md, sql/model/README.md.
"""
from __future__ import annotations

import json
import math
import os
import re

from google.cloud import bigquery

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "qwiklabs-gcp-03-941c9ce79592")
GRID = f"{PROJECT}.a4i_grid"
BROOKLYN = f"{PROJECT}.a4i_brooklyn"
CELL = 0.001
MAX_CELLS = 200

# Demo cooling-access notes for three tracts, carried over from HeatRx. NOT verified facility
# data: walk times and transit options were prepared by hand for the demo.
DEMO_ACCESS = {
    "36047030300": ("Weeksville", 11.8, "No direct transit trip identified in the demo.",
                    "Confirm transfers and coordinate door-to-door outreach or partner rides."),
    "36047036900": ("Saratoga Library", 14.3, "Q24 planning option.",
                    "Verify stops and a backup route; hand out route cards through local partners."),
    "36047031300": ("Albany OAC", 11.1, "B65 planning option.",
                    "Verify admission and accessible entrance; coordinate an escorted trip."),
}

_client = None


def _bq() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client(project=PROJECT)
    return _client


def _query(sql: str, *params) -> list[dict]:
    cfg = bigquery.QueryJobConfig(query_parameters=list(params),
                                  maximum_bytes_billed=256_000_000, job_timeout_ms=20_000)
    rows = _bq().query_and_wait(sql, job_config=cfg, location="US", wait_timeout=25)
    return json.loads(json.dumps([dict(r) for r in rows], default=str, allow_nan=False))


def _cell(lat: float, lon: float) -> tuple[int, int]:
    return math.floor(lat / CELL), math.floor(lon / CELL)


def _in_nyc(lat: float, lon: float) -> bool:
    return 40.45 <= lat <= 40.95 and -74.3 <= lon <= -73.65


def _tract_geoid(tract: str) -> str | None:
    """'369', '303.01' or an 11-digit GEOID -> Brooklyn GEOID, or None."""
    t = str(tract).strip()
    m = re.fullmatch(r"([0-9]{1,4})(?:\.([0-9]{1,2}))?", t)
    if m:
        number, suffix = m.groups()
        t = f"36047{number.zfill(4)}{(suffix or '').ljust(2, '0')}"
    return t if re.fullmatch(r"36047[0-9]{6}", t) else None


def _parse_cells(cells: list[str]) -> list[tuple[int, int]] | None:
    out = []
    for c in cells[:MAX_CELLS]:
        try:
            i, j = (int(x) for x in str(c).replace("_", ",").split(","))
        except ValueError:
            return None
        out.append((i, j))
    return out


# ---------------------------------------------------------------------------- blocks
def get_block(lat: float, lon: float) -> dict:
    """Everything about the 0.001-degree block (~110 m x 85 m) containing a point, with a LIVE
    BigQuery ML prediction and its explanation.

    Returns the block id (cell_i, cell_j), place names (neighbourhood, council district,
    nearest street address), measured offset if a car drove there, the live model prediction
    with per-feature contributions in degrees F (ML.EXPLAIN_PREDICT on heat_offset_v1),
    confidence, estimated hot days per year, built environment and who lives there.

    Heat offset = degrees F hotter (+) or cooler (-) than the campaign-day average on one
    afternoon. Not a temperature: a day's block high ~= that day's city high + offset.

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees (negative in New York).
    """
    if not _in_nyc(lat, lon):
        return {"status": "outside_coverage", "note": "Only New York City is in the grid; the model covers Brooklyn."}
    ci, cj = _cell(lat, lon)
    keys = (bigquery.ScalarQueryParameter("ci", "INT64", ci), bigquery.ScalarQueryParameter("cj", "INT64", cj))
    profile = _query(f"""
        SELECT p.cell_i, p.cell_j, p.borough, p.tract_geo_id, p.is_land, p.confidence,
               p.measured_offset_f, p.offset_best_f, p.est_days_ge_90_per_year,
               p.est_days_ge_95_per_year, p.est_days_ge_100_per_year,
               p.trees_100m, p.tree_dbh_sum_100m, p.tract_water_share, p.dist_open_space_m,
               p.epa_walk_index, p.est_residents, p.est_residents_65_plus,
               p.est_households_no_car, p.bg_median_income, p.tract_no_vehicle_rate,
               p.tract_poverty_rate, pl.neighborhood, pl.council_district, pl.street_address
        FROM `{GRID}.cell_profile_full` p
        LEFT JOIN `{GRID}.cell_places` pl USING (cell_i, cell_j)
        WHERE p.cell_i = @ci AND p.cell_j = @cj
    """, *keys)
    if not profile:
        return {"status": "not_in_grid", "cell_i": ci, "cell_j": cj,
                "note": "Outside the analysis grid (Brooklyn plus ~1 km around the measured routes)."}
    live = _query(f"""
        SELECT predicted_offset_f, baseline_prediction_value AS baseline_offset_f,
               nbr_n AS measured_cells_within_1km,
               ARRAY(SELECT AS STRUCT feature, ROUND(attribution, 3) AS contribution_f
                     FROM UNNEST(top_feature_attributions)
                     ORDER BY ABS(attribution) DESC) AS contributions
        FROM ML.EXPLAIN_PREDICT(MODEL `{GRID}.heat_offset_v1`,
          (SELECT * FROM `{GRID}.cell_model_input` WHERE cell_i = @ci AND cell_j = @cj),
          STRUCT(6 AS top_k_features))
    """, *keys)
    return {
        "status": "ok",
        "block": profile[0],
        "live_prediction": live[0] if live else None,
        "notes": ("live_prediction is null for water/non-land cells. Contributions explain the "
                  "model relative to an average block; they are associations from one afternoon, "
                  "not causes. 'nbr_offset_1km' = heat of nearby measured streets."),
    }


def shortlist_blocks(area: str, limit: int = 10, include_predicted: bool = True,
                     low_tree_only: bool = False) -> dict:
    """Rank the hottest land blocks in one area, with who lives there. Field-check candidates,
    not verified stops.

    Args:
        area: A Brooklyn census tract ('369' or 11-digit GEOID), a neighbourhood name as in the
            2015 tree census (e.g. 'Bedford', 'Crown Heights North', 'Bushwick South'), or a
            council district as 'district 36'.
        limit: How many blocks, 1-50.
        include_predicted: Include model-predicted blocks (confidence 'near_data'). Measured
            blocks are always included; extrapolated ones never are.
        low_tree_only: Only blocks with no live street tree within 100 m.
    """
    if not 1 <= limit <= 50:
        return {"status": "limit_must_be_1_to_50"}
    a = area.strip()
    district = re.fullmatch(r"(?:council\s+)?district\s*(\d{1,2})", a, re.I)
    tract = _tract_geoid(a)
    if district:
        # council_district is stored as text ("33")
        where, param = "pl.council_district = @v", bigquery.ScalarQueryParameter("v", "STRING", str(int(district.group(1))))
    elif tract:
        where, param = "p.tract_geo_id = @v", bigquery.ScalarQueryParameter("v", "STRING", tract)
    else:
        where, param = "LOWER(pl.neighborhood) LIKE LOWER(@v)", bigquery.ScalarQueryParameter("v", "STRING", f"%{a}%")
    conf = "('measured','near_data')" if include_predicted else "('measured')"
    rows = _query(f"""
        SELECT p.cell_i, p.cell_j, ROUND(p.lat, 5) AS lat, ROUND(p.lon, 5) AS lon,
               pl.neighborhood, pl.council_district, pl.street_address,
               ROUND(p.offset_best_f, 2) AS offset_f, p.confidence, p.trees_100m,
               ROUND(p.est_residents) AS est_residents,
               ROUND(p.est_residents_65_plus) AS est_residents_65_plus,
               ROUND(p.est_households_no_car) AS est_households_no_car
        FROM `{GRID}.cell_profile_full` p
        LEFT JOIN `{GRID}.cell_places` pl USING (cell_i, cell_j)
        WHERE p.is_land AND p.borough = 'Brooklyn' AND {where}
          AND p.confidence IN {conf} AND (NOT @low_tree OR p.trees_100m = 0)
        ORDER BY p.offset_best_f DESC, p.cell_i, p.cell_j
        LIMIT @limit
    """, param, bigquery.ScalarQueryParameter("low_tree", "BOOL", low_tree_only),
        bigquery.ScalarQueryParameter("limit", "INT64", limit))
    return {
        "area": a, "blocks": rows,
        "cells": [f"{r['cell_i']},{r['cell_j']}" for r in rows],
        "limitations": ("Hottest first by measured offset where driven, else prediction. One "
                        "afternoon. Resident counts are block-group estimates spread over cells. "
                        "Street trees are not a shade measurement. Candidates for field checks."),
    }


def local_heat_history(lat: float, lon: float) -> dict:
    """Estimated days per year this block reached 90/95/100 F (2019-2025), next to the city
    reference observed at Central Park, LaGuardia and JFK.

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
    """
    if not _in_nyc(lat, lon):
        return {"status": "outside_coverage"}
    ci, cj = _cell(lat, lon)
    rows = _query(f"""
        SELECT local.year, local.est_days_ge_90, local.est_days_ge_95, local.est_days_ge_100,
               city.days_ge_90 AS city_days_ge_90, city.days_ge_95 AS city_days_ge_95,
               city.days_ge_100 AS city_days_ge_100
        FROM `{GRID}.cell_heat_history_all` local
        JOIN `{GRID}.city_heat_history` city USING (year)
        WHERE local.cell_i = @ci AND local.cell_j = @cj
        ORDER BY local.year
    """, bigquery.ScalarQueryParameter("ci", "INT64", ci), bigquery.ScalarQueryParameter("cj", "INT64", cj))
    return {
        "cell_i": ci, "cell_j": cj, "years": rows,
        "limitations": ("Block counts apply one afternoon's offset to daily station highs: estimates, "
                        "not observed block temperatures. Seven years is too short to call a trend."),
    }


def station_heat_history() -> dict:
    """Observed days per year at or above 95 F at each weather station near Brooklyn, with how
    many days each station reported (2019-2025)."""
    rows = _query(f"""
        SELECT station_id, ANY_VALUE(station_name) AS station_name,
               EXTRACT(YEAR FROM observation_date) AS year,
               COUNT(DISTINCT IF(max_temp_f IS NOT NULL, observation_date, NULL)) AS reporting_days,
               COUNT(DISTINCT IF(max_temp_f >= 95, observation_date, NULL)) AS days_ge_95
        FROM `{BROOKLYN}.station_daily_temps`
        GROUP BY station_id, year ORDER BY station_id, year
    """)
    return {"source": f"{BROOKLYN}.station_daily_temps (NOAA GHCN-Daily)", "station_years": rows}


# ---------------------------------------------------------------------------- tracts, equity
def tract_evidence(tract: str) -> dict:
    """Measured heat and ACS indicators for one Brooklyn census tract.

    Args:
        tract: Tract as written ('369', '303.01') or an 11-digit GEOID.
    """
    geoid = _tract_geoid(tract)
    if not geoid:
        return {"status": "invalid_brooklyn_tract"}
    rows = _query(f"""
        WITH heat AS (
          SELECT COUNT(measured_offset_f) AS measured_cells,
                 AVG(measured_offset_f) AS mean_measured_offset_f,
                 MAX(measured_offset_f) AS max_measured_offset_f,
                 COUNTIF(confidence = 'near_data') AS predicted_cells,
                 AVG(IF(confidence IN ('measured','near_data'), offset_best_f, NULL)) AS mean_offset_measured_or_predicted_f
          FROM `{GRID}.cell_profile_full`
          WHERE is_land AND tract_geo_id = @t
        )
        SELECT geo_id, tract_name, total_pop, share_65_plus, no_cars, no_vehicle_rate,
               poverty_rate, median_income, heat.*
        FROM `{GRID}.tract_exposure` CROSS JOIN heat
        WHERE geo_id = @t
    """, bigquery.ScalarQueryParameter("t", "STRING", geoid))
    return {
        "tract": rows[0] if rows else None,
        "access": demo_cooling_access(geoid) if geoid in DEMO_ACCESS else None,
        "caveat": "Measured cells are a campaign sample, not a tract-wide population-weighted average. ACS 2019 5-year.",
    }


def demo_cooling_access(tract: str) -> dict:
    """Demo cooling-center access notes for tracts 303, 313 and 369 only. Hand-prepared for the
    hackathon demo, NOT verified facility data: always tell the planner to confirm hours and
    access with NYC 311.

    Args:
        tract: Tract as written ('369') or an 11-digit GEOID.
    """
    geoid = _tract_geoid(tract)
    if geoid not in DEMO_ACCESS:
        return {"status": "no_demo_access_notes_for_tract",
                "note": "We have no facility list loaded; only tracts 303, 313 and 369 have demo notes."}
    center, walk, transit, rec = DEMO_ACCESS[geoid]
    return {"tract": geoid, "center": center, "estimated_walk_minutes": walk, "transit": transit,
            "recommendation": rec, "source": "Hackathon demo planning notes (unverified)."}


def equity_audit(cells: list[str]) -> dict:
    """Audit a set of recommended blocks: do they reach the people who need help most? Compares
    the residents of the given blocks with all of Brooklyn on age, car access, poverty and income.
    Run this on your own recommendations before presenting them.

    Args:
        cells: Recommended blocks as "cell_i,cell_j" strings (e.g. from shortlist_blocks).
    """
    parsed = _parse_cells(cells or [])
    if not parsed:
        return {"status": "give_cells_as_cell_i,cell_j"}
    rows = _query(f"""
        WITH picked AS (
          SELECT * FROM `{GRID}.cell_profile_full`
          WHERE FORMAT('%d,%d', cell_i, cell_j) IN UNNEST(@cells)
        ),
        cohorts AS (
          SELECT 'recommended' AS cohort, * FROM picked
          UNION ALL
          SELECT 'brooklyn', * FROM `{GRID}.cell_profile_full` WHERE is_land AND borough = 'Brooklyn'
        )
        SELECT cohort, COUNT(*) AS blocks,
               ROUND(SUM(est_residents)) AS residents,
               ROUND(SAFE_DIVIDE(SUM(est_residents_65_plus), SUM(est_residents)), 3) AS share_65_plus,
               ROUND(SAFE_DIVIDE(SUM(est_households_no_car), SUM(est_residents)), 3) AS no_car_households_per_resident,
               ROUND(SAFE_DIVIDE(SUM(tract_poverty_rate * est_residents),
                                 SUM(IF(tract_poverty_rate IS NULL, 0, est_residents))), 3) AS poverty_rate,
               ROUND(SAFE_DIVIDE(SUM(bg_median_income * est_residents),
                                 SUM(IF(bg_median_income IS NULL, 0, est_residents)))) AS resident_weighted_median_income,
               ROUND(AVG(IF(confidence IN ('measured','near_data'), offset_best_f, NULL)), 2) AS mean_offset_f
        FROM cohorts GROUP BY cohort ORDER BY cohort DESC
    """, bigquery.ArrayQueryParameter("cells", "STRING", [f"{i},{j}" for i, j in parsed]))
    return {
        "comparison": rows,
        "limitations": ("Resident-weighted estimates (block-group ACS 2018, tract ACS 2019). The model "
                        "never uses demographics; race is not loaded. Shows who the plan reaches, not "
                        "historical neglect."),
    }


# ---------------------------------------------------------------------------- the model
def model_card() -> dict:
    """How good is the heat model? Held-out score of heat_offset_v1 (live from BigQuery), the
    5-fold block cross-validation, and the team's earlier baseline comparison."""
    held_out = _query(f"SELECT * EXCEPT (evaluated_at) FROM `{GRID}.model_evaluation`")
    return {
        "deployed_model": "a4i_grid.heat_offset_v1 (LINEAR_REG)",
        "held_out": held_out[0] if held_out else None,
        "cross_validation": {"folds": 5, "pooled_mae_f": 0.822, "mean_r2": 0.317, "worst_fold_r2": 0.22,
                             "baseline_mae_f": 1.036, "source": "sql/model/cross_validate.sh, 2026-11-17"},
        "earlier_baselines_same_188_cells": {"coordinates_only_mae_f": 1.280, "trees_and_land_mae_f": 1.264,
                                             "nearby_streets_mae_f": 0.941,
                                             "source": "sql/model/06_matched_brooklyn_benchmark.sql (archived)"},
        "epa_features": "Adding 6 EPA Smart Location features did not improve cross-validation (0.825 vs 0.822 F).",
        "limitations": ("One afternoon of volunteer-driven readings. Half the model's weight is 'nearby "
                        "measured streets', so blocks far from a route are close to unknown. Shows "
                        "associations, not what an intervention would do."),
    }


DEMO_TRACTS = tuple(DEMO_ACCESS)


def outreach_equity() -> dict:
    """Compare the three demo outreach tracts with Brooklyn overall using ACS and measured campaign heat."""
    rows = _query(f"""
        WITH measured AS (
            SELECT tract_geo_id, COUNT(measured_offset_f) AS measured_cells,
                   AVG(measured_offset_f) AS mean_measured_offset_f
            FROM `{GRID}.cell_profile_full`
            WHERE borough = 'Brooklyn' AND is_land AND tract_geo_id IN UNNEST(@tracts)
            GROUP BY tract_geo_id
        ),
        tracts AS (
            SELECT geo_id, total_pop, pop_65_plus, households, no_cars, median_income
            FROM `{GRID}.tract_exposure`
            WHERE borough = 'Brooklyn'
        ),
        cohorts AS (
            SELECT scope, tracts.*
            FROM tracts
            CROSS JOIN UNNEST(['Brooklyn', 'Demo shortlist']) AS scope
            WHERE scope = 'Brooklyn' OR geo_id IN UNNEST(@tracts)
        ),
        summary AS (
            SELECT scope, COUNT(*) AS tract_count,
                   SAFE_DIVIDE(SUM(IF(households > 0, no_cars, NULL)),
                       SUM(IF(households > 0 AND no_cars IS NOT NULL, households, NULL))) AS no_vehicle_rate,
                   SAFE_DIVIDE(SUM(IF(total_pop > 0, pop_65_plus, NULL)),
                       SUM(IF(total_pop > 0 AND pop_65_plus IS NOT NULL, total_pop, NULL))) AS share_65_plus,
                   SAFE_DIVIDE(SUM(median_income * households),
                       SUM(IF(median_income IS NOT NULL, households, NULL))) AS household_weighted_average_of_tract_median_incomes
            FROM cohorts
            GROUP BY scope
        )
        SELECT ARRAY(SELECT AS STRUCT * FROM summary ORDER BY scope) AS comparison,
               ARRAY(SELECT AS STRUCT tracts.*, COALESCE(measured_cells, 0) AS measured_cells,
                            mean_measured_offset_f
                     FROM tracts LEFT JOIN measured ON geo_id = tract_geo_id
                     WHERE geo_id IN UNNEST(@tracts) ORDER BY geo_id) AS shortlist
    """, bigquery.ArrayQueryParameter("tracts", "STRING", list(DEMO_TRACTS)))
    return {
        **rows[0],
        "access_plans": {tract_id: demo_cooling_access(tract_id) for tract_id in DEMO_TRACTS},
        "sources": [f"{GRID}.tract_exposure (ACS 2019 5-year)", f"{GRID}.cell_profile_full"],
        "income_display_label": "Household-weighted average of tract median incomes",
        "limitations": (
            "Fixed demo shortlist, not a validated borough-wide ranking. Income is a household-weighted "
            "average of tract medians, not the borough median. Excludes records with missing values in each "
            "rate. This is an aggregate coverage check, not evidence of historical neglect, race equity, "
            "or which older individuals lack a car."
        ),
    }


DATA_TOOLS = [get_block, shortlist_blocks, local_heat_history, station_heat_history,
              tract_evidence, demo_cooling_access, equity_audit, model_card, outreach_equity]
