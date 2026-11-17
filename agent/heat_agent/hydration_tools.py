"""Hydration-truck tools on the team's k-means model and deployment tables (read-only).

  Model   hydration_ml.hydration_location_clusters   BigQuery ML KMEANS: groups candidate sites into zones
  Tables  hydration_results.truck_location_candidates  latitude, longitude, neighborhood, heat_risk_score,
                                                        vulnerability_score, population_65_plus, ...
          hydration_results.daily_truck_deployment      truck_number, deployment_date, cluster_id, street_address,
                                                        predicted_temp_f, hydration_priority_score, deployment_status
          (block_vulnerability, clustered_blocks: supporting; reachable through the BigQuery MCP server)

ML.PREDICT on a KMEANS model returns CENTROID_ID (the zone) and NEAREST_CENTROIDS_DISTANCE, passing
the input columns through. The candidates table is assumed to hold the model's feature columns; if
the model was trained on another table, change CANDIDATES_FOR_MODEL below.
"""
from __future__ import annotations

import functools
import os

from google.api_core.exceptions import GoogleAPIError
from google.cloud import bigquery

from .data_tools import PROJECT, _in_nyc, _query

MODEL = os.environ.get("HYDRATION_MODEL", f"{PROJECT}.hydration_ml.hydration_location_clusters")
RESULTS = os.environ.get("HYDRATION_RESULTS_DS", f"{PROJECT}.hydration_results")
CANDIDATES = f"{RESULTS}.truck_location_candidates"
DEPLOYMENTS = f"{RESULTS}.daily_truck_deployment"
CANDIDATES_FOR_MODEL = os.environ.get("HYDRATION_MODEL_INPUT", CANDIDATES)


def _safe(fn):
    """Return a clear status instead of an exception if a table/column differs from what we expect."""
    @functools.wraps(fn)  # keeps the signature/docstring ADK turns into the tool schema
    def wrap(*a, **k):
        try:
            return fn(*a, **k)
        except GoogleAPIError as e:
            return {"status": "query_failed", "error": str(e)[:300],
                    "hint": "Check table/column names in hydration_tools.py against hydration_results / hydration_ml."}
    return wrap


@_safe
def hydration_zones() -> dict:
    """The hydration-truck zones from the team's k-means model (hydration_location_clusters): for each
    zone, number of candidate sites, centre point, average heat risk and vulnerability, people 65+, and
    the neighbourhoods it covers. Highest-need zones first. Use to explain where trucks should be based."""
    rows = _query(f"""
        WITH z AS (
          SELECT CENTROID_ID AS zone, * EXCEPT (CENTROID_ID, NEAREST_CENTROIDS_DISTANCE)
          FROM ML.PREDICT(MODEL `{MODEL}`, (SELECT * FROM `{CANDIDATES_FOR_MODEL}`))
        )
        SELECT zone, COUNT(*) AS candidate_sites,
               ROUND(AVG(latitude), 5) AS center_lat, ROUND(AVG(longitude), 5) AS center_lon,
               ROUND(AVG(heat_risk_score), 3) AS avg_heat_risk_score,
               ROUND(AVG(vulnerability_score), 3) AS avg_vulnerability_score,
               ROUND(SUM(population_65_plus)) AS population_65_plus,
               ARRAY_AGG(DISTINCT neighborhood IGNORE NULLS LIMIT 6) AS neighborhoods
        FROM z GROUP BY zone
        ORDER BY avg_heat_risk_score * avg_vulnerability_score DESC
    """)
    return {"status": "ok", "model": MODEL, "zones": rows,
            "note": "Zones come from k-means on the candidate sites: groupings for planning, not verified routes."}


@_safe
def top_hydration_sites(limit: int = 10, neighborhood: str = "", min_vulnerability: float = 0.0) -> dict:
    """Top candidate locations for a hydration truck, with their k-means zone, ranked by heat risk ×
    vulnerability, then people 65+.

    Args:
        limit: How many sites, 1-50.
        neighborhood: Optional neighbourhood filter (partial match), '' for all.
        min_vulnerability: Only sites with vulnerability_score at or above this value.
    """
    limit = max(1, min(int(limit), 50))
    rows = _query(f"""
        SELECT CENTROID_ID AS zone, ROUND(latitude, 5) AS lat, ROUND(longitude, 5) AS lon, neighborhood,
               ROUND(heat_risk_score, 3) AS heat_risk_score, ROUND(vulnerability_score, 3) AS vulnerability_score,
               ROUND(population_65_plus) AS population_65_plus
        FROM ML.PREDICT(MODEL `{MODEL}`, (SELECT * FROM `{CANDIDATES_FOR_MODEL}`))
        WHERE (@nb = '' OR LOWER(neighborhood) LIKE LOWER(CONCAT('%', @nb, '%')))
          AND IFNULL(vulnerability_score, 0) >= @minv
        ORDER BY heat_risk_score * vulnerability_score DESC, population_65_plus DESC
        LIMIT @lim
    """, bigquery.ScalarQueryParameter("nb", "STRING", neighborhood.strip()),
        bigquery.ScalarQueryParameter("minv", "FLOAT64", float(min_vulnerability)),
        bigquery.ScalarQueryParameter("lim", "INT64", limit))
    return {"status": "ok", "sites": rows,
            "cells": [f"{int(r['lat'] // 0.001)},{int(r['lon'] // 0.001)}" for r in rows if r.get("lat")],
            "note": "Candidate sites for field checks. Run equity_audit on 'cells' before presenting."}


@_safe
def truck_deployment_plan(deployment_date: str = "", truck_number: int = 0, limit: int = 30) -> dict:
    """The team's recorded hydration-truck deployments (daily_truck_deployment): truck, date, zone
    (cluster_id), street address, predicted block temperature, priority score and status. Highest
    priority first. These are the plan rows stored in BigQuery, not live truck positions.

    Args:
        deployment_date: 'YYYY-MM-DD', or '' for the latest date in the table.
        truck_number: A truck number, or 0 for all trucks.
        limit: Max rows, 1-100.
    """
    limit = max(1, min(int(limit), 100))
    rows = _query(f"""
        WITH d AS (
          SELECT * FROM `{DEPLOYMENTS}`
          WHERE CAST(deployment_date AS STRING) = IF(@dt = '',
                  (SELECT CAST(MAX(deployment_date) AS STRING) FROM `{DEPLOYMENTS}`), @dt)
            AND (@truck = 0 OR truck_number = @truck)
        )
        SELECT truck_number, CAST(deployment_date AS STRING) AS deployment_date, cluster_id, street_address,
               ROUND(predicted_temp_f, 1) AS predicted_temp_f,
               ROUND(hydration_priority_score, 3) AS hydration_priority_score, deployment_status
        FROM d ORDER BY hydration_priority_score DESC LIMIT @lim
    """, bigquery.ScalarQueryParameter("dt", "STRING", deployment_date.strip()),
        bigquery.ScalarQueryParameter("truck", "INT64", int(truck_number)),
        bigquery.ScalarQueryParameter("lim", "INT64", limit))
    by_truck: dict = {}
    for r in rows:
        by_truck.setdefault(r["truck_number"], []).append(r)
    return {"status": "ok" if rows else "no_rows", "deployments": rows,
            "trucks": {str(k): len(v) for k, v in by_truck.items()},
            "note": "predicted_temp_f is the hydration pipeline's estimate; cite it as such."}


@_safe
def hydration_zone_for_point(lat: float, lon: float) -> dict:
    """Which hydration zone (k-means cluster) a Brooklyn point falls in, via the nearest candidate site.

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
    """
    if not _in_nyc(lat, lon):
        return {"status": "outside_coverage"}
    rows = _query(f"""
        SELECT CENTROID_ID AS zone, neighborhood, ROUND(latitude, 5) AS site_lat, ROUND(longitude, 5) AS site_lon,
               ROUND(ST_DISTANCE(ST_GEOGPOINT(longitude, latitude), ST_GEOGPOINT(@lon, @lat))) AS distance_m,
               ROUND(heat_risk_score, 3) AS heat_risk_score, ROUND(vulnerability_score, 3) AS vulnerability_score
        FROM ML.PREDICT(MODEL `{MODEL}`, (SELECT * FROM `{CANDIDATES_FOR_MODEL}`))
        ORDER BY distance_m LIMIT 1
    """, bigquery.ScalarQueryParameter("lat", "FLOAT64", lat), bigquery.ScalarQueryParameter("lon", "FLOAT64", lon))
    return {"status": "ok" if rows else "no_candidates", "nearest_candidate": rows[0] if rows else None}


HYDRATION_TOOLS = [hydration_zones, top_hydration_sites, truck_deployment_plan, hydration_zone_for_point]
