# Brooklyn Heat Portal

A planner-facing front end for the heat assistant. It starts with a **map view** that overlays
every feature in the `a4i_grid` dataset on Brooklyn, plus one view per decision from the team's
planning sheet (Today, Decade, Equity audit, Heat history, Ask the agent).

Each view lists the persona questions it will answer and how far the data gets us today:
**Ready now** (9 of 20), **Needs agent** (7) or **Needs external data** (4). That catalogue
lives in [`backend/personas.py`](backend/personas.py). Update a status there when the model, the
agent or a dataset lands.

## Run it locally

```bash
cd portal
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r pyproject.toml
.venv/bin/python -m backend.data refresh      # BigQuery -> data/ snapshot (~10 s)
.venv/bin/uvicorn backend.main:app --reload --port 8080
```

Open http://localhost:8080.

`refresh` uses your **gcloud CLI** login (`gcloud auth print-access-token`), not
application-default credentials, because ADC is often a different Google account than the
Qwiklabs one. Set `GCP_PROJECT` if your project isn't `qwiklabs-gcp-03-941c9ce79592`.

Re-run `refresh` whenever `sql/grid/build.sh` or `sql/model/run.sh` rebuilds the tables. The
snapshot reads `a4i_grid.cell_profile_full`, which `sql/model/run.sh` creates, so run the model
pipeline at least once first.

To run a second copy next to another session's server, use port 8081
(`.claude/launch.json` has a `heat-portal-8081` entry).

## What's where

| Path | What it does |
|---|---|
| `backend/main.py` | FastAPI: `/api/cells`, `/api/tracts`, `/api/heat-days`, `/api/model`, `/api/forecast`, `/api/personas`, `/api/chat`, and serves `frontend/` |
| `backend/chat.py` | `POST /api/chat`: runs the ADK agent (`../agent/heat_agent`) and streams its reply as server-sent events; `GET /api/chat/contract` |
| `frontend/js/chat.js` | Chat panel: streaming, tool chips, sends the planner's map state with each message |
| `frontend/js/ui_tools.js` | Browser handlers for the agent's UI tools (zoom to a block, switch layer, outline blocks, …) |
| `backend/data.py` | BigQuery → compact JSON snapshot in `data/` (git-ignored) |
| `backend/forecast.py` | National Weather Service daytime highs for Brooklyn (live, cached 30 min) |
| `backend/personas.py` | Views and the 20 persona questions, each with a data-readiness status |
| `frontend/js/layers.js` | Map layer catalogue: label, palette, scale, notes. **Add a feature here** |
| `frontend/js/map.js` | MapLibre map: cells, tracts, overlays, legend, inspector |
| `frontend/js/views.js` | The non-map views, generated from the persona catalogue |

No front-end build step: plain ES modules plus MapLibre GL from a CDN.

## Design decisions to defend

- **Basemap: USGS The National Map**, which is US Government work in the public domain.
  OpenStreetMap-based tiles would raise the ODbL share-alike question the challenge README warns about.
- **Cells are drawn in the browser** from `(cell_i, cell_j)` — the same 0.001° grid as the SQL —
  so the payload stays ~3 MB raw (much smaller gzipped) for 29.5k cells.
- **Demographics are shown per block but labelled as not model inputs.** Block-group values are
  ACS 2018, car ownership and poverty are tract-level ACS 2019; the inspector says which.
- **Default layer is the heat offset, measured or predicted**, with extrapolated blocks (nothing
  measured within 1 km) greyed out. The model barely beats "average" there, so the map shouldn't
  imply it knows.
- **Forecast overlay = NWS daytime high + the block's offset.** The offset was measured against
  the city average on one afternoon, so it pairs with a daily high, not an hourly temperature.
  `api.weather.gov` is US Government work (public domain, no key); we send a generic User-Agent
  as NWS asks. Off-season the forecast is mild, so the layer also offers a **what-if scenario**
  (default 102°F, the sheet's "102°F Thursday"), clearly labelled as not a forecast. Exposure
  counts (blocks, residents 65+, households without a car at ≥ 95/100/105°F) leave out
  extrapolated blocks.
- **Every block explains itself.** The inspector shows `ML.EXPLAIN_PREDICT` contributions in °F,
  in plain words, with the caveat that half the weight is "nearby measured streets".
- **Snapshot, not live queries.** The demo doesn't depend on BigQuery latency, and the front-end
  lane can work without credentials.

## The agent chat

The right-hand side panel has three tabs: **💬 Assistant** (the default, on every view),
**Layers** and **Block** (map view only; clicking a block opens Block). The assistant starts
with **"I am planning as"**, a persona picker built from the planning sheet's roles
(`backend/personas.py`), and that role's questions as one-click starters. The persona goes to
the agent in `context.persona`.

The backend runs the ADK agent in `../agent/heat_agent` in-process (Gemini on Vertex AI in the
team project) and streams back server-sent events:

| Event | Payload | The UI should |
|---|---|---|
| `session` | `{session_id, mode}` | keep `session_id`, send it with the next message |
| `text` | `{delta}` | append to the reply |
| `tool_call` | `{id, name, args, ui}` | show a chip for data tools (`ui: false`) |
| `tool_result` | `{id, name, response}` | mark that chip done |
| `ui_action` | `{id, name, args}` | run `HANDLERS[name](args)` in `js/ui_tools.js` |
| `error` | `{message}` | show it |
| `done` | `{}` | re-enable input |

Request: `POST /api/chat {message, session_id?, context?}`. `context` is what the planner sees
(view, layer, selected block, map centre, persona); the agent reads it as `ui_context`.

### Ranked locations

For ranked answers ("top 5 blocks for wellness checks", dispatch candidates) the agent calls the
UI tool `show_ranked_locations`, and the map draws **numbered markers** (1 = top) and fits to
them. Each location follows the ranked-location contract first written for the portal's demo
`/api/ask` endpoint:

```json
{
  "rank": 1,
  "cell_i": 40644, "cell_j": -73968,
  "label": "182 Westminster Road",
  "metric": "est_residents_65_plus", "value": 31.2, "unit": "residents",
  "layer": "est_residents_65_plus",
  "source": "measured | estimated | predicted | external"
}
```

Give the block as `cell_i`/`cell_j` (preferred: it's what the data tools return) or a point as
`lat`/`lon`. `layer`, if given, switches the map to that layer. Up to 20 locations. The agent
is told to list the places in its reply in the same order, so the numbers match. Ranked markers
survive clicking through the blocks; `clear_map_highlights` removes them.

**Building the UI without Gemini:** start the server with `AGENT_MODE=mock`. Replies are
scripted (and say so) but use the same events, including real `ui_action`s against real
cells: ask "how hot is it", "show top sites" (numbered markers on the five hottest measured
blocks) or "switch layer". `.claude/launch.json` has a `heat-portal-mock` entry (port 8090).

How the agent moves the map, and how to add a UI tool, is in [`../agent/README.md`](../agent/README.md).
Conversations live in memory (a restart forgets them); swap the session service in
`backend/chat.py` before relying on that.

## Adding a layer

1. Add the column to `CELL_COLUMNS` or `TRACT_COLUMNS` in `backend/data.py`.
2. `python -m backend.data refresh`
3. Add an entry to `LAYER_GROUPS` in `frontend/js/layers.js`.

Model inputs that appear in the "why" breakdown need a plain-language name in
`FEATURE_LABELS` (`frontend/js/layers.js`). If you retrain `heat_offset_v1` with new features,
add them there and update the cross-validation numbers in `backend/data.py` (`"cv"`).

## Deploy to Cloud Run

Deploy from the **repo root**: the root `Dockerfile` packages `portal/` and `agent/` together.

```bash
gcloud run deploy heat-portal --source . --region us-central1 --min-instances 1 \
  --set-env-vars GOOGLE_CLOUD_PROJECT=<project-id>,GCP_PROJECT=<project-id>
```

The service account needs **BigQuery Data Viewer + BigQuery Job User** (tables, the live
`ML.EXPLAIN_PREDICT`, and the managed BigQuery MCP server) and **Vertex AI User** (Gemini).
`portal/data/` (the map snapshot) is uploaded with the source (root `.gcloudignore`); if it's
missing, the first request builds it. `--min-instances 1` avoids a cold start during the demo.
Keep the service private (IAM-required invocation): `/api/chat` has no rate limiting.

`portal/Dockerfile` still builds the map-only portal, without the agent.
