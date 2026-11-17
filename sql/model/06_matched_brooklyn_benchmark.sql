-- Run in the project containing the archived Brooklyn snapshots and baseline model.
CREATE TABLE IF NOT EXISTS `a4i_brooklyn.brooklyn_matched_benchmark_inputs` AS
WITH examples AS (
  SELECT f.cell_i, f.cell_j, f.lat, f.lon, h.offset_f,
         f.trees_100m, f.trees_250m, f.tree_dbh_sum_100m, f.dist_open_space_m,
         f.tract_water_share, f.tract_housing_units_per_km2,
         MOD(FARM_FINGERPRINT(FORMAT('%d:%d', CAST(FLOOR(f.lat * 100) AS INT64),
                                    CAST(FLOOR(f.lon * 100) AS INT64))), 5) = 0 AS holdout,
         FORMAT('%d:%d', CAST(FLOOR(f.cell_i / 10) AS INT64),
                         CAST(FLOOR(f.cell_j / 10) AS INT64)) AS block_id,
         ST_GEOGPOINT(f.lon, f.lat) AS center
  FROM `a4i_brooklyn.brooklyn_feature_snapshot_for_demo` f
  JOIN `a4i_brooklyn.brooklyn_label_snapshot_for_benchmark` h
  USING (cell_i, cell_j)
  WHERE f.borough = 'Brooklyn' AND f.is_land AND f.is_driven
    AND h.offset_f IS NOT NULL AND f.lat IS NOT NULL AND f.lon IS NOT NULL
    AND f.trees_100m IS NOT NULL AND f.trees_250m IS NOT NULL
    AND f.tree_dbh_sum_100m IS NOT NULL AND f.dist_open_space_m IS NOT NULL
    AND f.tract_water_share IS NOT NULL AND f.tract_housing_units_per_km2 IS NOT NULL
), neighbors AS (
  SELECT target.cell_i, target.cell_j, AVG(source.offset_f) AS nbr_offset_1km, COUNT(*) AS nbr_n
  FROM examples target
  JOIN examples source
    ON NOT source.holdout AND target.block_id != source.block_id
    AND ST_DWITHIN(target.center, source.center, 1000)
  GROUP BY 1, 2
)
SELECT e.* EXCEPT(center, block_id), b.bg_housing_units_per_km2,
       COALESCE(n.nbr_offset_1km, 0) AS nbr_offset_1km, COALESCE(n.nbr_n, 0) AS nbr_n
FROM examples e
JOIN `a4i_grid.cell_built` b USING (cell_i, cell_j)
LEFT JOIN neighbors n USING (cell_i, cell_j);

ASSERT (SELECT COUNTIF(NOT holdout) = 588 AND COUNTIF(holdout) = 188
               AND COUNT(*) = COUNT(DISTINCT FORMAT('%d:%d', cell_i, cell_j))
        FROM `a4i_brooklyn.brooklyn_matched_benchmark_inputs`)
  AS 'Expected the exact original Brooklyn sample and one row per cell';

CREATE MODEL IF NOT EXISTS `a4i_brooklyn.brooklyn_nearby_matched_baseline`
OPTIONS (MODEL_TYPE='LINEAR_REG', INPUT_LABEL_COLS=['offset_f'], DATA_SPLIT_METHOD='NO_SPLIT') AS
SELECT nbr_offset_1km, tract_water_share, dist_open_space_m,
       bg_housing_units_per_km2, trees_100m, tree_dbh_sum_100m, offset_f
FROM `a4i_brooklyn.brooklyn_matched_benchmark_inputs`
WHERE NOT holdout;

WITH
ours AS (
  SELECT ROUND(mean_absolute_error, 3) AS mae, ROUND(SQRT(mean_squared_error), 3) AS rmse
  FROM ML.EVALUATE(MODEL `a4i_brooklyn.brooklyn_grid_tree_land_baseline`,
    (SELECT lat, lon, trees_100m, trees_250m, tree_dbh_sum_100m, dist_open_space_m,
            tract_water_share, tract_housing_units_per_km2, offset_f
     FROM `a4i_brooklyn.brooklyn_matched_benchmark_inputs` WHERE holdout))
), teammate AS (
  SELECT ROUND(mean_absolute_error, 3) AS mae, ROUND(SQRT(mean_squared_error), 3) AS rmse
  FROM ML.EVALUATE(MODEL `a4i_brooklyn.brooklyn_nearby_matched_baseline`,
    (SELECT nbr_offset_1km, tract_water_share, dist_open_space_m, bg_housing_units_per_km2,
            trees_100m, tree_dbh_sum_100m, offset_f
     FROM `a4i_brooklyn.brooklyn_matched_benchmark_inputs` WHERE holdout))
), coverage AS (
  SELECT COUNTIF(holdout AND nbr_n > 0) AS supported_holdout_cells,
         ROUND(AVG(IF(holdout, ABS(offset_f - nbr_offset_1km), NULL)), 3) AS neighbors_only_mae
  FROM `a4i_brooklyn.brooklyn_matched_benchmark_inputs`
)
SELECT ours.mae AS ours_mae, teammate.mae AS nearby_mae, ours.rmse AS ours_rmse,
       teammate.rmse AS nearby_rmse, supported_holdout_cells, neighbors_only_mae
FROM ours, teammate, coverage;
