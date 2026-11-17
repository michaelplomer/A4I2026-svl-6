-- a4i_grid.cell_places
-- PLACE NAMES, per cell, borrowed from the 2015 street tree census: every tree was recorded
-- with its street address, neighbourhood (NTA), council district, zip code and community
-- board. Trees line nearly every street, so the trees near a cell say what people call it.
--
--   neighborhood, council_district, zipcode, community_board
--       the most common value among live/dead trees and stumps within 150 m of the cell centre
--   street_address   address of the single closest tree, e.g. "412 NOSTRAND AVENUE"; read it
--                    as "near ...": it is where a tree stands, not a geocoded building
--   street_address_dist_m, n_trees_150m   how far that tree is / how many trees voted
--   trees_alive, trees_poor, trees_dead, stumps
--                    census records whose point falls INSIDE the cell (no overlap between
--                    cells, so they add up). Dead trees and stumps are ready planting sites.
--
-- Limits:
--   * A vote, not an official boundary lookup: cells on a district line may get the neighbour's.
--   * NULL where no tree is within 150 m (park interiors, rail yards, waterfront, water).
--   * 2015 boundaries. NTAs were redrawn in 2020 and council districts in 2023.

CREATE OR REPLACE TABLE a4i_grid.cell_places
CLUSTER BY cell_i, cell_j AS
WITH
trees AS (
  SELECT
    address, nta_name, CAST(cncldist AS STRING) AS council_district,
    CAST(zipcode AS STRING) AS zipcode, CAST(cb_num AS STRING) AS community_board,
    ST_GEOGPOINT(longitude, latitude) AS g
  FROM nyc_trees.tree_census_2015
  WHERE latitude IS NOT NULL AND longitude IS NOT NULL
),
near AS (
  SELECT c.cell_i, c.cell_j, t.*, ST_DISTANCE(c.center, t.g) AS d
  FROM a4i_grid.cells c
  JOIN trees t ON ST_DWITHIN(c.center, t.g, 150)
),
voted AS (
  SELECT
  cell_i,
  cell_j,
  APPROX_TOP_COUNT(nta_name, 1)[OFFSET(0)].value         AS neighborhood,
  APPROX_TOP_COUNT(council_district, 1)[OFFSET(0)].value AS council_district,
  APPROX_TOP_COUNT(zipcode, 1)[OFFSET(0)].value          AS zipcode,
  APPROX_TOP_COUNT(community_board, 1)[OFFSET(0)].value  AS community_board,
  ARRAY_AGG(address IGNORE NULLS ORDER BY d LIMIT 1)[SAFE_OFFSET(0)] AS street_address,
  MIN(d)                                                 AS street_address_dist_m,
  COUNT(*)                                               AS n_trees_150m
FROM near
GROUP BY cell_i, cell_j
),
in_cell AS (
  SELECT
    CAST(FLOOR(latitude / 0.001) AS INT64)  AS cell_i,
    CAST(FLOOR(longitude / 0.001) AS INT64) AS cell_j,
    COUNTIF(status = 'Alive')                     AS trees_alive,
    COUNTIF(status = 'Alive' AND health = 'Poor') AS trees_poor,
    COUNTIF(status = 'Dead')                      AS trees_dead,
    COUNTIF(status = 'Stump')                     AS stumps
  FROM nyc_trees.tree_census_2015
  WHERE latitude IS NOT NULL AND longitude IS NOT NULL
  GROUP BY 1, 2
)
SELECT
  c.cell_i,
  c.cell_j,
  v.* EXCEPT (cell_i, cell_j),
  IFNULL(t.trees_alive, 0) AS trees_alive,
  IFNULL(t.trees_poor, 0)  AS trees_poor,
  IFNULL(t.trees_dead, 0)  AS trees_dead,
  IFNULL(t.stumps, 0)      AS stumps
FROM a4i_grid.cells c
LEFT JOIN voted v   USING (cell_i, cell_j)
LEFT JOIN in_cell t USING (cell_i, cell_j);
