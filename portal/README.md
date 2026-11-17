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
| `backend/main.py` | FastAPI: `/api/cells`, `/api/tracts`, `/api/heat-days`, `/api/model`, `/api/personas`, `/api/ask` (501 until the agent is wired), and serves `frontend/` |
| `backend/data.py` | BigQuery → compact JSON snapshot in `data/` (git-ignored) |
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
- **Every block explains itself.** The inspector shows `ML.EXPLAIN_PREDICT` contributions in °F,
  in plain words, with the caveat that half the weight is "nearby measured streets".
- **Snapshot, not live queries.** The demo doesn't depend on BigQuery latency, and the front-end
  lane can work without credentials.

## Adding a layer

1. Add the column to `CELL_COLUMNS` or `TRACT_COLUMNS` in `backend/data.py`.
2. `python -m backend.data refresh`
3. Add an entry to `LAYER_GROUPS` in `frontend/js/layers.js`.

Model inputs that appear in the "why" breakdown need a plain-language name in
`FEATURE_LABELS` (`frontend/js/layers.js`). If you retrain `heat_offset_v1` with new features,
add them there and update the cross-validation numbers in `backend/data.py` (`"cv"`).

## Deploy to Cloud Run

```bash
cd portal
gcloud run deploy heat-portal --source . --region us-central1 --min-instances 1 --allow-unauthenticated
```

The `data/` snapshot is uploaded with the source (see `.gcloudignore`). If it's missing, the
first request builds it from BigQuery with the service account, which needs BigQuery Data
Viewer and Job User roles. `--min-instances 1` avoids a cold start during the demo.
