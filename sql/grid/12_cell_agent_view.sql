-- a4i_grid.cell_agent_view
-- One flat row per grid cell with the columns the agent needs, under plain names.
-- Needs the model outputs (sql/model/run.sh) and a4i_grid.cell_places (11_cell_places.sql).
--
--   cell_id              'cell_i_cell_j', e.g. '40652_-73950'
--   latitude, longitude  cell centre
--   neighborhood, council_district, street_address
--                        from the 2015 tree census (see 11_cell_places.sql); street_address
--                        is the closest tree's address, so read it as "near ..."
--   predicted_temp_f     campaign-mean afternoon temperature (~90.1 F, NOAA Heat Watch
--                        Brooklyn) + offset_best_f: what this block would read on that
--                        afternoon. Measured where driven, model-predicted elsewhere.
--   days_ge_90, days_ge_95   estimated days per year at or above 90 / 95 F, averaged over
--                        2019-2025 (reference station highs + offset_best_f)
--   population, population_65_plus, households_no_car
--                        estimated residents in the cell (block-group / tract ACS 2019 shares)
--   confidence           'measured' | 'near_data' | 'extrapolated'
--   in_training_range    inside the area the model learned from
--
-- Model-derived columns are NULL for water cells (is_land = FALSE).

CREATE OR REPLACE VIEW a4i_grid.cell_agent_view AS
WITH
campaign AS (  -- campaign mean temperature: cell temp minus its offset, same for every cell
  SELECT AVG(campaign_temp_f - offset_f) AS mean_temp_f FROM a4i_grid.cell_heat
),
hist AS (
  SELECT cell_i, cell_j,
         AVG(est_days_ge_90) AS days_ge_90,
         AVG(est_days_ge_95) AS days_ge_95
  FROM a4i_grid.cell_heat_history_all
  GROUP BY 1, 2
)
SELECT
  FORMAT('%d_%d', c.cell_i, c.cell_j)          AS cell_id,
  c.lat                                        AS latitude,
  c.lon                                        AS longitude,
  pl.neighborhood,
  pl.council_district,
  pl.street_address,
  c.is_land,
  campaign.mean_temp_f + p.offset_best_f       AS predicted_temp_f,
  CAST(h.days_ge_90 AS FLOAT64)                AS days_ge_90,
  CAST(h.days_ge_95 AS FLOAT64)                AS days_ge_95,
  i.est_residents                              AS population,
  i.est_residents_65_plus                      AS population_65_plus,
  i.est_households_no_car                      AS households_no_car,
  p.confidence,
  c.in_training_range
FROM a4i_grid.cells c
CROSS JOIN campaign
LEFT JOIN a4i_grid.cell_heat_predicted p USING (cell_i, cell_j)
LEFT JOIN hist h                         USING (cell_i, cell_j)
LEFT JOIN a4i_grid.cell_impact i         USING (cell_i, cell_j)
LEFT JOIN a4i_grid.cell_places pl        USING (cell_i, cell_j);
