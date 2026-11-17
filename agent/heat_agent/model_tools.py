"""Model registry: every BigQuery ML heat model the team trained, read live, plus side-by-side
predictions for one point. Answers "which models did you try, and why this one?".

Primary (deployed) model: a4i_grid.heat_offset_v1 (get_block / placement_recommendation call it).
SVL-6 experiments in a4i_brooklyn (see docs/heatrx/HeatRx_Agent_Context.md, section 5):

  svl6_heat_offset_geo_baseline  LINEAR_REG  latitude, longitude                     random split (overstates)
  svl6_geo_fair_baseline         LINEAR_REG  latitude, longitude                     geographic holdout
  svl6_heat_offset_trees_v2      LINEAR_REG  latitude, longitude, trees_within_200m  geographic holdout
  svl6_heat_boosted_v3           BOOSTED     latitude, longitude, trees_within_200m  use only once evaluated

Reported scores (team notes, re-checked live where BigQuery has them):
  geo_baseline random-split MAE 0.888 F; geo_fair MAE 1.329 F, R2 -0.090; trees_v2 MAE 1.366 F, R2 -0.159.
"""
from __future__ import annotations

import os

from google.api_core.exceptions import GoogleAPIError
from google.cloud import bigquery

from .data_tools import GRID, PROJECT, _in_nyc, _query

BROOKLYN_DS = f"{PROJECT}.a4i_brooklyn"
TREES = os.environ.get("HEAT_TREES_TABLE", f"{PROJECT}.nyc_trees.tree_census_2015")

REGISTRY = [
    {"id": "P", "model": f"{GRID}.heat_offset_v1", "role": "PRIMARY: all answers and rankings",
     "inputs": "nearby measured streets, water share, open space, housing density, street trees",
     "evaluation_note": "held-out + 5-fold block CV (see model_card)", "uses_trees_200m": False},
    {"id": "1", "model": f"{BROOKLYN_DS}.svl6_heat_offset_geo_baseline", "role": "demo of ML.EXPLAIN_PREDICT only",
     "inputs": "latitude, longitude", "evaluation_note": "random split MAE 0.888 F (overstates: nearby readings leak)",
     "uses_trees_200m": False},
    {"id": "1B", "model": f"{BROOKLYN_DS}.svl6_geo_fair_baseline", "role": "baseline for the geographic holdout",
     "inputs": "latitude, longitude", "evaluation_note": "geographic holdout MAE 1.329 F, R2 -0.090",
     "uses_trees_200m": False},
    {"id": "2", "model": f"{BROOKLYN_DS}.svl6_heat_offset_trees_v2", "role": "evidence: tree count did not help",
     "inputs": "latitude, longitude, trees_within_200m", "evaluation_note": "geographic holdout MAE 1.366 F, R2 -0.159",
     "uses_trees_200m": True},
    {"id": "H", "model": f"{PROJECT}.hydration_ml.hydration_location_clusters",
     "role": "hydration-truck zones (KMEANS); used by hydration_tools, not an offset model",
     "inputs": "candidate-site features (hydration_results.truck_location_candidates)",
     "evaluation_note": "clustering: ML.EVALUATE gives Davies-Bouldin index / mean squared distance",
     "uses_trees_200m": False},
    {"id": "3", "model": f"{BROOKLYN_DS}.svl6_heat_boosted_v3", "role": "candidate: do not use until evaluated",
     "inputs": "latitude, longitude, trees_within_200m", "evaluation_note": "unknown", "uses_trees_200m": True},
]


def _model_exists(fq: str) -> bool:
    try:
        bigquery.Client(project=PROJECT).get_model(fq)
        return True
    except GoogleAPIError:
        return False


def model_registry() -> dict:
    """All heat models the team trained in BigQuery ML: role, inputs, whether each exists right now,
    and its stored evaluation (ML.EVALUATE) next to the team's reported score. Use for "which models
    did you try?" and "why this model?". Scores from different holdout splits are not comparable."""
    out = []
    for m in REGISTRY:
        row = {k: m[k] for k in ("id", "model", "role", "inputs", "evaluation_note")}
        row["exists"] = _model_exists(m["model"])
        if row["exists"]:
            try:
                ev = _query(f"SELECT * FROM ML.EVALUATE(MODEL `{m['model']}`)")
                row["ml_evaluate"] = ev[0] if ev else None
            except GoogleAPIError as e:
                row["ml_evaluate"] = {"error": type(e).__name__}
        out.append(row)
    return {
        "models": out,
        "reading": ("Coordinates alone (1B) do worse than predicting the average on unseen areas (R2 < 0); "
                    "adding the 2015 tree count (2) did not help. The primary model adds features that "
                    "describe each block and is the only one used for recommendations. Compare scores only "
                    "within the same split."),
    }


def compare_model_predictions(lat: float, lon: float) -> dict:
    """Predict the heat offset for one Brooklyn point with every available model, side by side, so a
    planner can see how much the answer depends on the model. The tree count for models 2/3 is
    computed from the 2015 census (live trees within 200 m), never guessed. Recommendations still
    use only the primary model.

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
    """
    if not _in_nyc(lat, lon):
        return {"status": "outside_coverage"}
    p_lat = bigquery.ScalarQueryParameter("lat", "FLOAT64", lat)
    p_lon = bigquery.ScalarQueryParameter("lon", "FLOAT64", lon)
    trees = _query(f"""
        SELECT COUNT(*) AS n FROM `{TREES}`
        WHERE status = 'Alive' AND ST_DWITHIN(ST_GEOGPOINT(longitude, latitude), ST_GEOGPOINT(@lon, @lat), 200)
    """, p_lat, p_lon)
    n_trees = trees[0]["n"] if trees else None
    results = []
    for m in REGISTRY:
        row = {"id": m["id"], "model": m["model"].split(".")[-1], "role": m["role"]}
        if not _model_exists(m["model"]):
            row["status"] = "missing"
        elif m["id"] in ("3", "H"):
            row["status"] = "skipped_until_evaluated" if m["id"] == "3" else "not_an_offset_model"
        else:
            try:
                if m["id"] == "P":
                    q = f"""SELECT predicted_offset_f AS pred FROM ML.PREDICT(MODEL `{m['model']}`,
                              (SELECT * FROM `{GRID}.cell_model_input`
                               WHERE cell_i = CAST(FLOOR(@lat / 0.001) AS INT64)
                                 AND cell_j = CAST(FLOOR(@lon / 0.001) AS INT64)))"""
                    res = _query(q, p_lat, p_lon)
                else:
                    extra = ", @trees AS trees_within_200m" if m["uses_trees_200m"] else ""
                    q = f"""SELECT * FROM ML.PREDICT(MODEL `{m['model']}`,
                              (SELECT @lat AS latitude, @lon AS longitude{extra}))"""
                    params = [p_lat, p_lon] + ([bigquery.ScalarQueryParameter("trees", "INT64", n_trees)]
                                               if m["uses_trees_200m"] else [])
                    res = _query(q, *params)
                    if res:
                        pred = next((v for k, v in res[0].items() if k.startswith("predicted_")), None)
                        res = [{"pred": pred}]
                row["predicted_offset_f"] = round(res[0]["pred"], 2) if res and res[0]["pred"] is not None else None
                row["status"] = "ok" if res else "no_row"
            except GoogleAPIError as e:
                row["status"] = f"error: {type(e).__name__}"
        results.append(row)
    return {"lat": lat, "lon": lon, "trees_within_200m_2015": n_trees, "predictions": results,
            "note": ("Spread between models = model uncertainty. Use the primary model (P) for answers; "
                     "the SVL-6 models are comparison evidence.")}


MODEL_TOOLS = [model_registry, compare_model_predictions]
