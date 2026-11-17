-- a4i_grid.cell_agent_view
-- One flat row per grid cell with the columns the agent needs, under plain names. The agent
-- queries it through the managed BigQuery MCP server, and reads the column descriptions below
-- through get_table_info, so the descriptions are written for the model: say what a number
-- is, its unit, and what it is NOT.
-- Needs the model outputs (sql/model/run.sh) and a4i_grid.cell_places (11_cell_places.sql).
--
--   heat_offset_f             the headline heat number: degrees F above (+) / below (-) the
--                             campaign average on the measured afternoon. Measured where
--                             driven, model-predicted elsewhere (see confidence).
--   measured_heat_offset_f    the same, measured blocks only (NULL elsewhere)
--   campaign_afternoon_temp_f campaign-mean afternoon temperature (~90.1 F) + heat_offset_f:
--                             what this block read (or would have read) on that one afternoon.
--                             A temperature, not an offset and not a forecast. (Was
--                             predicted_temp_f, renamed because the agent averaged it and
--                             reported the result as an offset.)
--   days_ge_90, days_ge_95    estimated days per year at or above 90 / 95 F, 2019-2025
--   population, population_65_plus, households_no_car   estimated, allocated per cell
--   confidence                'measured' | 'near_data' | 'extrapolated'
--
-- Model-derived columns are NULL for water cells (is_land = FALSE).

CREATE OR REPLACE VIEW a4i_grid.cell_agent_view (
  cell_id OPTIONS (description = "Block id 'cell_i_cell_j', e.g. '40652_-73950'. cell_i = floor(lat/0.001), cell_j = floor(lon/0.001)."),
  latitude OPTIONS (description = "Latitude of the block centre (WGS 84)."),
  longitude OPTIONS (description = "Longitude of the block centre (WGS 84)."),
  neighborhood OPTIONS (description = "Neighbourhood name (NTA) from the 2015 street tree census; NULL where no tree is near."),
  council_district OPTIONS (description = "NYC council district number as text, from the 2015 tree census; NULL or 'Unassigned' where unknown."),
  street_address OPTIONS (description = "Address of the nearest street tree: read as 'near ...', not a verified site address."),
  is_land OPTIONS (description = "TRUE for land blocks. Heat, history and population columns are NULL or 0 when FALSE: always filter is_land."),
  heat_offset_f OPTIONS (description = "HEADLINE HEAT NUMBER. Degrees F hotter (+) or cooler (-) than the citywide campaign average on ONE measured afternoon (NOAA Heat Watch). Measured where confidence='measured', model-predicted otherwise. An offset, not a temperature. Use this to rank or compare heat."),
  measured_heat_offset_f OPTIONS (description = "heat_offset_f, but only for blocks a Heat Watch car measured (confidence='measured'); NULL elsewhere. Use for questions about measured heat."),
  campaign_afternoon_temp_f OPTIONS (description = "A TEMPERATURE in F, not an offset: campaign-mean afternoon temperature (~90.1 F) + heat_offset_f, i.e. what this block read on that one afternoon. Not a forecast. Do not call it or its average an offset."),
  days_ge_90 OPTIONS (description = "Estimated days per year at or above 90 F, averaged over 2019-2025 (reference station high + heat_offset_f). An estimate, not an observation."),
  days_ge_95 OPTIONS (description = "Estimated days per year at or above 95 F, averaged over 2019-2025. An estimate, not an observation."),
  population OPTIONS (description = "Estimated residents in this block: block-group population spread evenly over its land blocks. Sum it over blocks; do not average."),
  population_65_plus OPTIONS (description = "Estimated residents aged 65+ in this block (allocated like population). Sum over blocks."),
  households_no_car OPTIONS (description = "Estimated households without a car in this block (tract rate x allocated households). Sum over blocks."),
  confidence OPTIONS (description = "'measured' (driven), 'near_data' (predicted from measured streets within 1 km, typical error ~0.8 F) or 'extrapolated' (nothing measured within 1 km: treat heat as unknown)."),
  in_training_range OPTIONS (description = "TRUE when the block is within 1 km of a measured block, i.e. inside the area the model learned from.")
)
OPTIONS (description = "One row per Brooklyn grid block (0.001 deg, ~110 m x 85 m) for the planning agent: heat, estimated hot days, who lives there, place names. Heat values describe ONE campaign afternoon relative to the city average. When aggregating by area, report how many blocks each group has and treat groups with fewer than ~20 blocks as too small to rank.")
AS
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
  p.offset_best_f                              AS heat_offset_f,
  p.measured_offset_f                          AS measured_heat_offset_f,
  campaign.mean_temp_f + p.offset_best_f       AS campaign_afternoon_temp_f,
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
