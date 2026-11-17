# HeatRx: Agent Plan (v4, revised against the data we actually built)

> **"Send trucks Thursday. Fix the cause by 2035."**
>
> A Brooklyn heat-planning agent built on a BigQuery ML model that predicts **how much hotter each block runs than the Brooklyn average** (the *offset*). It says **how sure it is**, shows **who lives there**, and turns that into a dispatch plan for tomorrow and a replanting plan for the decade, each delivered as a **Placement Recommendation**.

| | |
|---|---|
| **City** | Brooklyn, NY |
| **Tracks** | ⚡ **Today** (emergency management) + 🌳 **Decade** (Parks / urban forestry) |
| **Link between tracks** | Blocks that keep needing trucks are the blocks to fix permanently |
| **Grid** | `cells`: 29,503 cells (~100 m), 20,127 on land. Any coordinate → cell via `FLOOR(lat/0.001)`, `FLOOR(lon/0.001)` |

---

## 0. What changed from v3, and why

| Finding from the build | What it changes in the plan |
|---|---|
| **The model is modest:** held-out MAE **0.95°F** vs **1.27°F** for predicting the average (≈25% better), R² **0.40**; cross-validation MAE **0.82°F**, R² **0.32** | We present it honestly as *"meaningfully better than guessing, not precise."* Answers lead with the **confidence label**, and rankings favor measured / near-measured blocks. |
| **Confidence has 3 levels:** measured (1,020 blocks), near measured streets (10,628), extrapolated (8,479) | `offset_best_f` uses the measurement where one exists. Extrapolated blocks get a caveat, and **never top a ranking without being flagged.** |
| **Attributions include spatial-context features** (e.g., "+0.6°F from nearby measured streets", "−0.4°F from waterfront") | "Why it's hot" separates **physical causes** (trees, built form) from **location context** (waterfront, the surrounding area). A prescription is made only from physical causes, and the agent says so when context dominates. |
| **EPA Smart Location features didn't help** (each correlates < 0.08 with the offset; adding them didn't improve the model) | Dropped from the model, which is a **rigor point** ("we tested it and kept the simpler model"). Kept as **context** in `cell_built` (walkability, transit distance, road density). |
| **Offsets are small, a few °F at most** | Demo numbers come from the real tables, never invented. "Dangerous days" uses the 90 / 95 / 100°F thresholds, so a small offset can still matter near a threshold. |
| **The 7-year trend is too short to rely on** | Shown only with a caveat; never used for ranking. |
| **The heat-index offset exists for measured blocks only** | "Feels like" is given only where measured; elsewhere the agent uses the temperature offset and says so. |
| **The tree census holds unused gold:** 6,459 poor-health trees, 3,319 dead, 4,230 stumps; council district (17), neighborhood (52), street addresses | **New Decade tool:** replacement priority. Stumps and dead trees are **existing tree pits at exact coordinates**, the most practical "spot to stand on" in the challenge. **New labeling step:** every cell gets a district, a neighborhood and a nearby address, so "my district" and "our neighborhood" work without extra external data. |
| Unused EPA columns: transit frequency (`D4C`), density detail | **New:** `transit_access` for "can people without a car get there?" |
| Forecast high is not in the data | NWS forecast stays a **live** tool; otherwise the planner types it in. |

---

## 1. Agent team

```
🕕 Sentinel (Cloud Scheduler, Wow tier) ──► watches the NWS forecast, drafts a heat-day briefing
                                   │
                ┌──────── HeatRx Root (router + session memory) ────────┐
                │ works out role, track, area; collects constraints      │
                └──────┬───────────────────┬────────────────────┬───────┘
          ⚡ Dispatch agent       🌳 Planning agent        🛡️ Review crew
             (Today)               (Decade)            ├─ Equity Auditor (runs on every ranked list)
                                                       ├─ Site Inspector (Street View + Gemini vision)
                                                       └─ Council Skeptic (uses model_evaluation)
                └──────────────── shared tools ─────────────────┘
                         │                         │
                   BigQuery + BQML           live APIs (NWS, Google Maps)
```

---

## 2. Data the agent uses (actual tables)

| Table | Grain | What it gives the agent |
|---|---|---|
| `cells` | ~100 m cell (29,503; 20,127 land) | Borough, block-group and tract IDs, land/water flag, distance to the nearest measured street (`in_training_range`) |
| `cell_heat_predicted` | Cell | `offset_best_f` (measured if available, else predicted), `confidence`, **`ML.EXPLAIN_PREDICT` attributions** (°F per feature) |
| `cell_profile_full` | Cell | Everything per cell in one row (the fast path for tools) |
| `cell_built` | Cell | Street trees within 50 / 100 / 250 m, total trunk diameter within 100 m, tract water share, block-group housing density, distance to large open space, 6 EPA features (context) |
| `cell_impact` | Cell | **Additive:** estimated residents, residents 65+, households without a car. **Block group (ACS 2018):** share 65+, median age, income, renter share, severe rent burden, density. **Tract (ACS 2019):** households without a car, poverty rate. *Never model inputs.* |
| `city_heat_history` | Year | Citywide days at or above 90 / 95 / 100°F and the peak, 2019–2025; 2,557 daily highs averaged over 3 stations |
| `cell_heat_history_all` | Cell × year (140,889 rows) | Estimated dangerous days per year per land cell; 7-year trend (caveated) |
| `blockgroup_exposure`, `tract_exposure` | Block group / tract | Demographics + boundaries for the **equity audit** |
| `model_evaluation` | Model run | Held-out and CV scores (MAE, R²) |
| `cell_readings` | Reading (15,105) | Raw temperature, heat index, humidity |
| `epa_sld.smart_location_nyc` | Block group | Extra EPA columns: transit frequency `D4C`, density `D1A/B/C`, road density `D3A/D3APO`, EPA rankings |
| Tree census (BigQuery public) | Tree | **Health / status** (poor, dead, stump), council district, neighborhood, street address |

### To add (one SQL step each)

| New table | Built from | Unlocks |
|---|---|---|
| `cell_labels` | Nearest tree-census record per cell → `council_district`, `nta_name` (neighborhood), `nearest_address` | "Is my district on the list?", "our neighborhood", addresses instead of coordinates |
| `cell_replacement` | Tree census per cell: `poor_trees_100m`, `dead_trees_100m`, `stumps_100m`, plus the coordinates of each stump / dead tree | Forester: "which blocks need replacements first" |
| `cell_transit` | EPA `D4C` (service frequency) + `D4A` (distance) per cell | "Can people without a car get there?" |
| `cell_risk` (view) | `cell_profile_full` + `cell_impact` + `cell_heat_history_all` + `cell_labels` + `cell_replacement` | One row per cell for ranking: offset, confidence, dangerous days, vulnerable residents, `person_heat_days`, `today_priority`, `decade_priority` |

---

## 3. Tool roster (revised)

*T# and D# in the "Answers" column are the Today and Decade question numbers from the question bank.*

| ID | Tool | Agent | Tier | Reads | Answers |
|---|---|---|---|---|---|
| S1 | `locate(place)` → cell or area. Addresses via geocoding; **districts and neighborhoods via `cell_labels`** | All | **Core** | `cells`, `cell_labels`, geocoding (live) | T4, T5 |
| **S2** | **`explain_heat(lat, lng)`** → `offset_best_f`, confidence, **physical causes vs. location context** in °F | All | **Core** | `cell_heat_predicted` | T4, T8, D2 |
| S3 | `compare_locations(a, b)` | Planning | Strong | S2 run on both | D2, T4 |
| S4 | `dangerous_days(location)` at 90 / 95 / 100°F vs. citywide (trend shown only with a caveat) | All | **Core** | `cell_heat_history_all`, `city_heat_history` | D5 |
| S5 | `who_lives_here(location)` | All (MCP Toolbox) | **Core** | `cell_impact` | T2, all |
| S6 | `coverage_check(location)` → measured / near / extrapolated + distance to the nearest measured street | All | **Core** | `cells`, `cell_heat_predicted` | T10, D10 |
| S7 | `person_heat_days(cells)`: residents × dangerous days × vulnerability | All | **Core** | `cell_risk` | D4 |
| S8 | `hotspot_search(area, filters)` (hottest, few trees, few residents, confidence ≥ near) | All | Strong | `cell_risk` | T6 |
| T1 | `heat_day_briefing(forecast_high)`: forecast + offset per block | Dispatch, Sentinel | Strong | NWS (live) or typed in, `cell_risk` | T8 |
| **T2** | **`dispatch_plan(trucks, teams, hours, area)`** + "what changed" summary; ranks by excess heat × residents 65+ and households without a car | Dispatch | **Core** | `cell_risk` | T1, T2, T5, T7 |
| T3 | `assign_zones` (KMEANS) + Google Maps route links | Dispatch | Strong | `cell_risk` | T1 |
| T4 | `transit_access(location)` | Dispatch | Strong | `cell_transit` | external #4 (no-car access) |
| T5 | `public_alert(area, languages)` | Dispatch | Wow | Gemini | T9 |
| **D1** | **`prescribe(location)`**: physical cause → fix (few trees → planting; dead tree / stump nearby → **replacement**; dense, heavily paved area → cool pavement / roofs as *candidates*). If location context dominates, it says "cause unclear; target by exposure." | Planning | **Core** | `cell_heat_predicted`, `cell_built`, `cell_replacement` | D3, D7 |
| **D2** | **`replacement_priority(area, n)`** 🆕: stumps / dead / poor trees ranked by heat offset × vulnerable residents, **with exact coordinates and addresses** | Planning | **Core** | `cell_replacement`, `cell_risk`, `cell_labels` | D1, Forester |
| D3 | `rank_interventions(type, budget, area)` → relief per dollar | Planning | Strong | `cell_risk`, sourced costs | D1, D4 |
| D4 | `chronic_hotspots(area)`: dangerous days per year by block | Both | Strong | `cell_heat_history_all` | D7 |
| D5 | `association_estimate(location, +trees)`, **always labeled "association, not forecast"** (only if trees carry real weight in the model) | Planning | Strong | model weights | D10 |
| D6 | `inspect_site(lat, lng)`: Street View → Gemini vision (shade, asphalt, empty tree pit) | Site Inspector | Strong | Street View (live) | D1, D7 |
| R1 | `equity_audit(cells)`: recommended areas vs. Brooklyn | Auditor | **Core** | `blockgroup_exposure`, `tract_exposure` | D6 |
| R2 | `draft_council_memo(...)` | Planning | Strong | all of the above | D8 |
| **R3** | **`build_placement_recommendation(...)`** | Both | **Core** | all of the above | T1, D1, D8 |
| V1 | `model_card()`: MAE 0.95 vs 1.27 baseline, R² 0.40, CV 0.82 / 0.32, confidence counts, limitations | All | **Core** | `model_evaluation` | D9 |

**README requirements:**
- **Tools you built:** S2, T2, D1, **D2** and **R3** are custom Python tools.
- **Managed MCP server:** MCP Toolbox on Cloud Run (`min-instances=1`) for generic lookups.
- **BigQuery ML:** S2 and D1 read attributions produced by `ML.EXPLAIN_PREDICT`. For a coordinate not in the precomputed table, call `ML.EXPLAIN_PREDICT` live.

---

## 4. The BigQuery ML story (honest version)

> **"Our model predicts an offset, not a temperature: 'this block runs 2°F hotter than the rest of Brooklyn,' not 'it will be 104°F here.' Add tomorrow's forecast and you get a number for this block. Where a car actually drove, we use the measurement. Everywhere else we predict it, and we tell you how sure we are."**

| Point | Evidence |
|---|---|
| Predicts places nobody measured | 20,127 land cells covered; 1,020 measured |
| Better than guessing | Held-out MAE 0.95°F vs 1.27°F baseline (≈25% better); CV MAE 0.82°F |
| Honest about limits | R² 0.32–0.40, so most block-to-block variation is still unexplained; extrapolated blocks are labeled |
| Explains each prediction | `ML.EXPLAIN_PREDICT` attributions in °F, split into physical causes and location context |
| Tested richer data and rejected it | EPA built-environment features added nothing (correlation < 0.08), so the simpler model was kept |

**Before the demo, run `ML.GLOBAL_EXPLAIN`** and write down which features actually matter:
- **Tree features carry real weight** → the Decade story is "missing canopy drives heat" (with the attributions as evidence).
- **Mostly location context** → the Decade story is "target **where it's hot and who's exposed**; replace dead trees and stumps first because that's the cheapest, fastest canopy gain," and the agent does **not** claim trees cause the offset.

Both stories are winnable. The second is honest and practical, and judges reward that over overclaiming.

---

## 5. Placement Recommendation (updated card)

```
#1  <nearest address>, <neighborhood> · Council District <n>     <lat 5dp>, <lng 5dp>  [Street View ↗]
──────────────────────────────────────────────────────────────────────────────────────────
HEAT          +X.X°F vs. Brooklyn average · Confidence: MEASURED / NEAR MEASURED / EXTRAPOLATED
              N days/yr ≥ 95°F here vs. M citywide (2019–2025)
              [Today] Thursday forecast F°F → ~F+X°F on this block
WHY           Physical: +a.a°F <feature> · +b.b°F <feature>
              Location context: +c.c°F <feature>
              → Fix: <replace stump / plant / cool pavement candidate / cause unclear: target by exposure>
WHO           ~R residents · ~S aged 65+ · ~H households without a car · poverty P% (tract)
TREES         K trees within 100 m (Brooklyn median Q) · D dead · S stumps (exact pits listed)
SITE CHECK    Street View: <shade / asphalt / empty tree pit>
IMPACT        ~P person-heat-days/yr
```

**Full document:**
1. Summary
2. Ranked site cards (Decade cards add the **stump / dead-tree coordinates**)
3. **Equity check** from `blockgroup_exposure` / `tract_exposure`, with numbers
4. Council memo with a limitations block

**Limitations block:**
- Readings come from a single summer afternoon.
- Routes were chosen by volunteers.
- R² is 0.32–0.40, so much of the variation is unexplained.
- Extrapolated blocks are less certain.
- The trend is too short to rely on.
- ACS demographics are from 2018/2019.
- Tree effects are associations, not proof that planting causes cooling.

---

## 6. Guardrails (system prompt)

- Heat is always "°F above or below the Brooklyn average"; an absolute temperature appears only with a forecast, and is labeled.
- **Always state the confidence label.** Extrapolated blocks → caveat; never present them as measured.
- Prescribe only from **physical** attributions; if location context dominates, say "the model can't isolate a physical cause here."
- Never claim a fix will produce a specific cooling, and never forecast a year (e.g., 2035).
- Don't cite the 7-year trend as evidence of warming.
- Demographics never enter the model; race appears only in the equity audit.
- Every number carries its source: *(measured)*, *(model)*, *(station history)*, *(ACS 2018/2019)*, *(tree census)*, *(live: source, time)*.

---

## 7. Validation (the rigor story)

| Check | Status | Deck line |
|---|---|---|
| Held-out split | ✅ MAE 0.95 vs 1.27, R² 0.40 | "≈25% better than guessing the average" |
| Cross-validation | ✅ MAE 0.82, R² 0.32 | "Stable across folds" |
| EPA ablation | ✅ no gain | "We tested richer data and kept the simpler model" |
| Confidence coverage | ✅ 1,020 / 10,628 / 8,479 | "We tell you which answers are measured, near measured or extrapolated" |
| Manhattan/Bronx cross-borough test | ⏳ Strong tier | "Tested on a borough it never saw" |
| Equity audit of top-N lists | ⏳ Core | Numbers vs. Brooklyn |
| Street View check of the top sites | ⏳ Strong | "N of 10 confirmed visually" |

---

## 8. Build tiers (revised)

| Tier | Ships |
|---|---|
| **Core** | `cell_labels`, `cell_replacement`, `cell_risk`; S1, S2, S4, S5, S6, S7, T2, D1, **D2**, R1, R3, V1 |
| **Strong** | S3, S8, T1, T3, T4 (`cell_transit`), D3, D4, D5 (only if trees matter), D6, R2, cross-borough test |
| **Wow** | Sentinel, T5 alerts, Council Skeptic, map toggle |

- **Never cut:** S2 with confidence, `dispatch_plan`, `replacement_priority`, the equity audit, the Placement Recommendation, the model card.
- **Cut order if behind:** Skeptic → Sentinel → D5 → cross-borough test.

---

## 9. Video demo (about 3 minutes; all numbers pulled from the tables before recording)

1. **Today:** "Thursday 102°F, 4 trucks, 6 hours" → stops ranked by excess heat × residents 65+ and households without a car, with addresses, district labels and route links. "Truck 2 broke down" → re-plan.
2. **Honesty moment:** "How sure are you about stop #3?" → "Extrapolated: no car drove within X m. Here's the nearest measured block."
3. **The link:** "Which of these blocks keep coming back?" → dangerous days per year → hand off to Decade.
4. **Decade:** "Where should Parks replant first?" → stumps and dead trees on the hottest, most exposed blocks, **with exact coordinates** → Street View check → council memo.
5. **Rigor:** model card (≈25% better than baseline, R² 0.40, EPA tested and rejected) + equity numbers.
6. **Closing line:** "It tells you where people are suffering, how sure it is, and which empty tree pit to fill first."
