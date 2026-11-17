#!/usr/bin/env bash
# 5-fold BLOCK cross-validation of the heat-offset model. Each fold holds out a different
# ~20% of the ~1 km blocks, recomputes the neighbour feature from that fold's training cells
# only, trains, and scores the held-out blocks. Prints per-fold and pooled results next to
# the "predict the training mean" baseline. Creates and deletes temporary models cv_*.
#
#   bash sql/model/cross_validate.sh [project-id]
#
# Result on 2026-11-17 (Brooklyn campaign):
#   built features only        pooled MAE 1.00 F, mean R2 -0.01 (baseline MAE 1.04)
#   neighbour term only        pooled MAE 0.83 F, mean R2  0.29
#   neighbour + built (v1)     pooled MAE 0.83 F, mean R2  0.31, worst fold 0.22
set -euo pipefail

PROJECT="${1:-$(gcloud config get-value project 2>/dev/null)}"
BLK="FORMAT('%d:%d', CAST(FLOOR(cell_i/10) AS INT64), CAST(FLOOR(cell_j/10) AS INT64))"

declare -A FEATS=(
  [built_only]="tract_water_share, bg_housing_units_per_km2, trees_100m, tree_dbh_sum_100m"
  [nbr_only]="nbr_offset_1km"
  [v1]="nbr_offset_1km, tract_water_share, bg_housing_units_per_km2, trees_100m, tree_dbh_sum_100m"
)

data() {  # training_examples + neighbour feature computed without fold $1
  local k=$1
  echo "
  WITH c AS (
    SELECT t.*, MOD(ABS(FARM_FINGERPRINT($BLK)), 5) AS fold, $BLK AS blk,
           ST_GEOGPOINT((cell_j + 0.5) * 0.001, (cell_i + 0.5) * 0.001) AS g
    FROM a4i_grid.training_examples t),
  nb AS (
    SELECT t.cell_i, t.cell_j, AVG(n.offset_f) AS nbr_offset_1km
    FROM c t JOIN c n ON n.fold != $k AND n.blk != t.blk AND ST_DWITHIN(t.g, n.g, 1000)
    GROUP BY 1, 2)
  SELECT c.* EXCEPT (g, blk), IFNULL(nb.nbr_offset_1km, 0) AS nbr_offset_1km
  FROM c LEFT JOIN nb USING (cell_i, cell_j)"
}

run() {
  local name=$1 k=$2 m="a4i_grid.cv_${1}_${2}"
  bq --project_id="$PROJECT" query --use_legacy_sql=false --quiet \
    "CREATE OR REPLACE MODEL $m OPTIONS (model_type='LINEAR_REG', input_label_cols=['offset_f'],
       data_split_method='NO_SPLIT') AS
     SELECT ${FEATS[$name]}, offset_f FROM ($(data "$k")) WHERE fold != $k" >/dev/null 2>&1
  bq --project_id="$PROJECT" query --use_legacy_sql=false --format=csv \
    "SELECT '$name', $k, mean_absolute_error, r2_score,
       (SELECT COUNT(*) FROM ($(data "$k")) WHERE fold = $k),
       (SELECT AVG(ABS(offset_f - (SELECT AVG(offset_f) FROM ($(data "$k")) WHERE fold != $k)))
          FROM ($(data "$k")) WHERE fold = $k)
     FROM ML.EVALUATE(MODEL $m, (SELECT * FROM ($(data "$k")) WHERE fold = $k))" 2>/dev/null | tail -1
  bq --project_id="$PROJECT" rm -f -m "$m" >/dev/null 2>&1
}

OUT="$(mktemp)"
for name in "${!FEATS[@]}"; do for k in 0 1 2 3 4; do run "$name" "$k" >> "$OUT" & done; done
wait

python3 - "$OUT" <<'EOF'
import csv, sys, collections
rows = collections.defaultdict(list)
for r in csv.reader(open(sys.argv[1])):
    if len(r) == 6:
        rows[r[0]].append([r[0], int(r[1])] + [float(x) for x in r[2:]])
print(f"{'model':12s} {'fold':>4s} {'mae':>6s} {'r2':>7s} {'n':>5s} {'base_mae':>8s}")
for name, v in sorted(rows.items()):
    for _, k, mae, r2, n, b in sorted(v, key=lambda x: x[1]):
        print(f"{name:12s} {k:4d} {mae:6.3f} {r2:7.3f} {int(n):5d} {b:8.3f}")
    n = sum(x[4] for x in v)
    print(f"{name:12s} {'ALL':>4s} {sum(x[2]*x[4] for x in v)/n:6.3f} "
          f"{sum(x[3] for x in v)/len(v):7.3f} {int(n):5d} {sum(x[5]*x[4] for x in v)/n:8.3f}  "
          f"(mean R2, worst fold R2 {min(x[3] for x in v):.3f})")
EOF
rm -f "$OUT"
