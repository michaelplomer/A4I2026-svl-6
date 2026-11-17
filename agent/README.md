# Brooklyn heat planning agent

The portal's **Ask the agent** view calls HeatRx, a stateless Google ADK agent in
`portal/backend/heat_agent.py`. Gemini calls its named evidence tools through
`portal/backend/mcp_server.py`, a private stdio MCP process inside the portal
container. There is no separate public MCP endpoint or arbitrary SQL tool.
It uses the existing BigQuery data and does not
train models, write tables, change the public outreach map, or query Dallas.

| Question | Evidence |
| --- | --- |
| What drives heat at this coordinate? | Measured campaign offset, tree and land features, and exposure from `a4i_grid.cell_profile_full`; a live `ML.EXPLAIN_PREDICT` call to `a4i_grid.heat_offset_v1` |
| Who might need help accessing cooling? | Measured grid coverage and tract-level ACS 2019 indicators from `a4i_grid.tract_exposure`; a static cooling-center access plan for Tracts 303, 313, and 369 |
| How often was it very hot? | Observed station counts and reporting coverage from `a4i_brooklyn.station_daily_temps`; modeled local historical estimates are labeled separately |
| Which model worked best? | The archived shared holdout comparison: coordinate grid, tree/land, and matched nearby-readings models; not the deployed model's error |

The tree census is represented through existing grid aggregates, not another
raw scan per request. Earlier baselines remain comparison evidence rather than
additional inference calls. The agent does not predict individual health risk
or verify current cooling-center hours.

See [the workbook acceptance checklist](workbook-coverage.md) for all 20 core
questions, including the required handling of missing data and unsupported claims.

## Hackathon Workflow

- **Load & Explore:** Import `notebooks/heatrx_colab_enterprise.ipynb` into Colab
  Enterprise and run it against the existing US BigQuery datasets. It checks the
  three input schemas, Tract 369's measured coverage, and a live model explanation.
- **Build:** Google ADK + Gemini on Vertex AI + the HeatRx MCP server + BigQuery ML.
- **Deploy and Show:** Use the private Cloud Run portal and its **Ask the agent**
  view. Roll out this version to the existing private service before claiming
  the MCP path is running there. Keep IAM invocation required and leave the
  separate public outreach map unchanged.
- **Differentiate:** HeatRx separates measured heat from predictions and shows
  why a block scores as it does without treating model attributions as causes.

## Progress

The private MCP portal and Colab workflow passed the original Tract 369 and coordinate smoke tests. The newer equity, shortlist, and heat-history tools passed local MCP checks with mocked BigQuery; they are not yet live-validated or deployed.

## Run privately

Use Python 3.11+, Application Default Credentials (ADC) for the hackathon
account, and an available Gemini model ID. The identity needs Vertex AI
inference and permission to run BigQuery jobs and read the named Brooklyn
datasets and model in the US location. Keep the portal private before enabling
the agent; the route is not an authentication or rate-limiting boundary.

```sh
cd portal
export GCP_PROJECT='<your Google Cloud project ID>'
export GOOGLE_CLOUD_PROJECT="$GCP_PROJECT"
export GOOGLE_CLOUD_LOCATION=global
export GOOGLE_GENAI_USE_VERTEXAI=true
export HEAT_AGENT_MODEL='<Gemini model ID enabled in the lab>'
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8080
```

Locally, initialize ADC with `gcloud auth application-default login` if needed.
The existing portal map snapshot refresh uses the gcloud CLI identity locally;
the agent itself uses ADC. The API can be tested without the map snapshot:

```sh
curl -s http://localhost:8080/api/ask -H 'Content-Type: application/json' \
  -d '{"message":"What measured heat and cooling access should guide outreach in Tract 369?"}'
```

Then try a point copied from the Brooklyn map, ask why the offset is predicted
to be warmer or cooler, and verify that the live explanation tool ran. Finally,
ask for the model comparison and check that the answer distinguishes the
188-cell benchmark from production inference.
