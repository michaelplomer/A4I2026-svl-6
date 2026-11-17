# HeatRx: the heat-planning agent (ADK)

`heat_agent/` is an [Agent Development Kit](https://google.github.io/adk-docs/) agent on
Gemini (Vertex AI). It combines the HeatRx evidence tools (review branch
`heatrx-brooklyn-agent-review`) with the portal's streaming chat and map tools.

```
heat_agent/
  agent.py         root_agent: model, instruction (HeatRx rules + map guidance), tool list
  data_tools.py    purpose-built BigQuery tools, incl. a live ML.EXPLAIN_PREDICT  <- tools we built
  bigquery_mcp.py  Google's managed BigQuery MCP server, read-only               <- managed MCP
  ui_tools.py      drive the portal map (show_cell_on_map, ...)                  <- front-end contract
  .env.example     Vertex AI settings for adk web / adk run
```

## How it meets the required stack

| Requirement | Here |
|---|---|
| ADK + Gemini | `root_agent` in `agent.py`, Gemini 2.5 Flash on Vertex AI (`HEAT_AGENT_MODEL` to change) |
| **Consume a managed MCP server** (not author one) | `bigquery_mcp.py` connects to `https://bigquery.googleapis.com/mcp`. Only `list_table_ids`, `get_table_info` and `execute_sql_readonly` are exposed; the write-capable `execute_sql` and job tools are filtered out |
| At least one tool we built | the 8 functions in `data_tools.py` |
| Agent **calls** the BigQuery ML model | `get_block` runs `ML.EXPLAIN_PREDICT` on `a4i_grid.heat_offset_v1` live for the requested block |
| Deployed on Google Cloud | Cloud Run, from the repo-root `Dockerfile` (see `portal/README.md`) |

## Tools

| Tool | Answers |
|---|---|
| `get_block(lat, lon)` | One block: live prediction + per-feature contributions (°F), measured offset, confidence, place names, hot days, built environment, who lives there |
| `shortlist_blocks(area, limit, include_predicted, low_tree_only)` | Hottest blocks in a tract (`'369'`), neighbourhood (`'Bedford'`) or `'district 36'`, with residents 65+ and no-car households |
| `equity_audit(cells)` | Who the recommended blocks reach vs. all of Brooklyn (age, car access, poverty, income) |
| `local_heat_history(lat, lon)` / `station_heat_history()` | Estimated hot days per block per year vs. the observed city reference / per station |
| `tract_evidence(tract)` / `demo_cooling_access(tract)` | Tract heat + ACS; hand-made demo access notes for tracts 303, 313, 369 (unverified) |
| `model_card()` | Held-out score (live from `a4i_grid.model_evaluation`), cross-validation, earlier baselines |
| BigQuery MCP | Anything else, mostly against `a4i_grid.cell_agent_view` (one plain-named row per block) |

The acceptance checklist for all 20 questions in the planning sheet is in
[workbook-coverage.md](workbook-coverage.md).

## Run it

On its own, with ADK's dev UI (good for prompt work; the UI tools just return "sent_to_portal"):

```bash
cd agent
cp heat_agent/.env.example heat_agent/.env
../portal/.venv/bin/adk web          # http://localhost:8000, pick heat_agent
```

Inside the portal (the real experience: the map moves). From `portal/`:

```bash
.venv/bin/uvicorn backend.main:app --reload --port 8080
```

Open http://localhost:8080 and click **💬 Ask the agent**. The portal imports `root_agent`
from this folder, so edits here show up after the server reloads.

Both use Gemini on Vertex AI in the team project through your gcloud application-default
login (`gcloud auth application-default login` with the Qwiklabs account).

## Adding a data tool

A tool is a plain Python function with type hints and a docstring. ADK turns the docstring
into the tool description Gemini sees, so write it for the model: what it returns, units,
and when to use it.

```python
# data_tools.py
def dangerous_days(cell_i: int, cell_j: int) -> dict:
    """Estimated days per year this block reaches 90/95/100°F (2019-2025) ..."""
    ...

DATA_TOOLS = [get_block, ..., dangerous_days]
```

Use `_query(sql, *params)` in `data_tools.py`: parameterized, capped at 256 MB billed and 20 s.
If a question is a one-off, let the agent answer it through the BigQuery MCP server instead
of adding a tool.

Still to build from the planning sheet: `dispatch_plan` (zones per truck/team),
`rank_interventions`, `chronic_hotspots`, `draft_council_memo`, `locate` (address lookup).
Tables are documented in `sql/grid/README.md` and `sql/model/README.md`.

## UI tools: how the agent moves the map

`ui_tools.py` declares functions the agent can call to act on the planner's screen:

| Tool | What the browser does |
|---|---|
| `show_cell_on_map(cell_i, cell_j, zoom)` | Fly to a block, select it, open its details panel |
| `show_location_on_map(lat, lon, zoom)` | Pan/zoom without selecting |
| `set_map_layer(layer_id)` | Change what the map is coloured by |
| `highlight_cells_on_map(cells, label)` | Outline blocks (`"cell_i,cell_j"` strings) and fit to them |
| `clear_map_highlights()` | Remove the outline |
| `open_portal_view(view_id)` | Switch to map / today / decade / equity / history |

The Python bodies only validate arguments. The portal backend spots these calls in ADK's
event stream and forwards them to the browser, where `portal/frontend/js/ui_tools.js` runs
them. **To add one, add a function here (and to `UI_TOOLS`) and a handler with the same name
and arguments in `HANDLERS` in `ui_tools.js`.** `GET /api/chat/contract` lists the declarations.

The agent also sees what the planner sees: every message carries the current view, map
layer, selected block and map centre, stored in session state as `ui_context` and pulled into
the instruction via `{ui_context?}`. So "this block" just works.

## Added on branch `Dhinakar`: tracks, Placement Recommendation, MCP Toolbox, team mode

### New tools (`ops_tools.py`)

| Tool | Track | Answers |
|---|---|---|
| `dispatch_plan(forecast_high_f, n_trucks, stops_per_truck, area)` | ⚡ Today | Stops ranked by (forecast + offset − 90°F) × (65+ + no-car households) × confidence, ≥ 400 m apart, grouped into one zone per truck with a Google Maps route link. Re-call with fewer trucks → `what_changed` |
| `get_forecast()` | ⚡ Today | NWS daytime highs for Brooklyn (live, public domain) |
| `replacement_priority(area, n)` | 🌳 Decade | Dead trees and stumps (existing pits) on hot, exposed blocks, **exact coordinates + address** from the 2015 census |
| `rank_planting_sites(area, n)` | 🌳 Decade | Hot, exposed blocks with no street tree within 100 m |
| `inspect_site(lat, lon)` | 🌳 Decade | Street View + Gemini vision: shade, surfaces, empty pit / sidewalk room (needs `GOOGLE_MAPS_API_KEY`) |
| `placement_recommendation(track, cells?)` | both | The deliverable: site cards with **live `ML.EXPLAIN_PREDICT`** contributions split into physical drivers vs. location context, candidate action, hot days, residents, tree pits, limitations. Uses the session's last list if `cells` is omitted |

All read `a4i_grid` through `data_tools._query` (parameterized, 256 MB / 20 s caps, read-only).
Actions are recommended only from physical drivers (trees, open space, housing density), never
from `nbr_offset_1km` or water share, and no cooling effect is ever forecast.

### Second MCP server: MCP Toolbox for Databases

`toolbox/tools.yaml` defines five shaped queries (`block_profile`, `neighborhood_summary`,
`council_district_summary`, `trees_near_point`, `citywide_heat_history`). Deploy it once:

```bash
bash agent/toolbox/deploy.sh          # private Cloud Run service, min-instances=1
export TOOLBOX_URL=<printed URL>      # or --update-env-vars on the portal service
```

The agent attaches it with `McpToolset` (`toolbox_mcp.py`, ID-token auth). With `TOOLBOX_URL`
unset it is skipped and everything else works as before.

| Requirement | Now |
|---|---|
| Consume a managed MCP server | Google's BigQuery MCP server **and** MCP Toolbox on Cloud Run |
| A tool we defined | `data_tools.py`, `ops_tools.py`, and the 5 Toolbox SQL tools |

### Team mode (optional)

`HEAT_AGENT_MODE=team` swaps the single agent for a router (`heat_planner`) with
`dispatch_agent` (Today) and `planning_agent` (Decade, with a `council_skeptic` reviewer).
Every agent keeps the map tools. The default single agent is unchanged.

### Try it

```bash
cd agent && ../portal/.venv/bin/adk web
```

> "Thursday is 102°F and I have 4 trucks, where should they go?" → "Truck 2 broke down" →
> "Which dead trees should Parks replace first in Brownsville?" → "Build the placement
> recommendation and audit it."

Planning docs: `docs/heatrx/` (agent plan v4, agent context, 20 test questions).
