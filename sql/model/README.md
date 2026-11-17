# heat_offset_v1 — predicting heat where nobody measured

A BigQuery ML `LINEAR_REG` model that predicts each grid cell's **heat offset**: degrees F
above (+) or below (−) the city average during the measured afternoon. It is not a
temperature. A temperature for a given day is

```
estimated local high = that day's city high (NWS forecast, or station history) + offset
```

Run it after the grid is built (≈30 seconds, safe to re-run):

```bash
bash sql/grid/build.sh <project-id>
bash sql/model/run.sh <project-id>
bash sql/model/cross_validate.sh <project-id>     # optional: 5-fold robustness check
```

## Results

| | Held-out MAE | R² | vs. predicting the average |
|---|---:|---:|---:|
| **heat_offset_v1**, official holdout (188 cells in whole ~1 km blocks) | 0.95 °F | 0.40 | 25% lower error |
| **heat_offset_v1**, 5-fold block cross-validation (all 1,020 cells) | 0.82 °F | 0.32 mean (worst fold 0.22) | 21% lower error |
| Built-environment features only, 5-fold | 1.00 °F | 0.01 | 3% lower error |

**Report the cross-validated numbers.** The official holdout happens to be a favourable
split; five different splits are the honest estimate.

## Inputs

| Feature | What it is | Global importance |
|---|---|---:|
| `nbr_offset_1km` | Mean measured offset of training cells within 1 km, excluding the cell's own ~1 km block | 0.48 |
| `tract_water_share` | Share of the cell's tract that is water — near water runs cooler | 0.16 |
| `tree_dbh_sum_100m` | Summed street-tree trunk diameter within 100 m — bigger trees, cooler | 0.12 |
| `trees_100m` | Street-tree count within 100 m | 0.03 |
| `bg_housing_units_per_km2` | Block-group housing density | 0.02 |

Demographics are deliberately not inputs (it would make the equity audit circular). Distance
to open space was tried and dropped: weight 0, and the proxy mixes parks with paved open land.

### Why a "nearby measured streets" term

Heat varies over a few hundred metres: offsets of cells 150 m apart correlate at 0.80, at
600 m 0.40, at 1.2 km 0.19. The built-environment data we have today is too coarse to see that
(tract-level water share, block-group density), so on its own it barely beats the average. The
neighbour term carries the local pattern from measured streets into the unmeasured cells
around them.

It is built to avoid leakage: it never uses held-out cells, and it excludes the cell's own
block, so a training cell can't learn from near-duplicates on the same road. Undriven cells
get exactly the same definition.

## Outputs

| Object | Contents |
|---|---|
| `cell_model_input` | One row per land cell (20,127): the inputs above, plus label and holdout where measured |
| `heat_offset_v1` | The model |
| `model_evaluation` | Held-out MAE, R², baseline, % improvement |
| `cell_heat_predicted` | Every land cell: `predicted_offset_f`, `measured_offset_f`, **`offset_best_f`** (measured if driven, else predicted), `attributions` (each feature's contribution in °F), `confidence` |
| `cell_heat_history_all` | Estimated days ≥ 90/95/100 °F per cell per year, 2019–2025, for every land cell |
| `cell_profile_full` | Everything about a cell in one row, including the model's view. **The portal and the agent read this** |

`confidence` tells the agent how much to trust a number:

| Value | Cells | Meaning |
|---|---:|---|
| `measured` | 1,020 | A car drove it. Use the measurement |
| `near_data` | 10,628 | Within 1 km of measured streets. Typical error ≈ 0.8 °F |
| `extrapolated` | 8,479 | Nothing measured within 1 km. Built features only, which barely beat the city average: predictions sit near 0 (−1.4 to +0.3 °F). **The agent should say it doesn't know**, not quote the number |

## Limitations — put these in the deck

1. **One afternoon.** The offset comes from a single campaign window. It is not an hourly
   forecast, and the multi-year history assumes the pattern holds on every hot day.
2. **"Why" is still mostly "because the streets nearby measured hot."** The neighbour term
   carries half the model's weight. Attributions name water and tree size as real causes,
   but the built-environment explanation is thin. The EPA Smart Location Database (join on
   `bg_geo_id`) and a shoreline distance are the next features to add — they should shift
   weight from the neighbour term to explainable causes.
3. **Association, not causation.** Tree size correlates with cooler cells; the model cannot
   say what planting would do.
4. **Coverage.** 42% of land cells are `extrapolated`. The driven routes don't cover large
   parts of south and east Brooklyn.
5. **Possible route-timing noise.** Brooklyn readings carry no timestamps, so drift in air
   temperature during the hour of driving can't be separated from real spatial differences.

## Gotcha

After retraining, query `ML.GLOBAL_EXPLAIN` / `ML.WEIGHTS` with `bq query --nouse_cache`.
BigQuery may serve the previous model's cached result for an identical query text.
