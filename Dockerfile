# Cloud Run image for the portal + the ADK agent. Build from the REPO ROOT (it needs both
# portal/ and agent/):
#   gcloud run deploy heat-portal --source . --region us-central1 --min-instances 1
#
# The service account needs: BigQuery Data Viewer + Job User (tables, ML.EXPLAIN_PREDICT, the
# BigQuery MCP server) and Vertex AI User (Gemini). portal/data/ (the map snapshot) is baked in
# if present; otherwise the first request builds it from BigQuery.
FROM python:3.12-slim
WORKDIR /app/portal
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.30" \
    "google-cloud-bigquery>=3.25" "db-dtypes>=1.3" "google-adk>=2.14,<3" "mcp>=1.20,<2"
COPY portal/backend backend
COPY portal/frontend frontend
COPY portal/data data
COPY agent /app/agent
ENV PORT=8080 AGENT_DIR=/app/agent \
    GOOGLE_GENAI_USE_VERTEXAI=TRUE GOOGLE_CLOUD_LOCATION=us-central1
CMD exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}
