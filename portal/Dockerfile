# Superseded by the Dockerfile at the repo root, which also packages agent/ for the chat.
# Deploy from the repo root:  gcloud run deploy heat-portal --source . --region us-central1
# This file still builds the map-only portal (no agent) from portal/.
#
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.30" \
    "google-cloud-bigquery>=3.25" "db-dtypes>=1.3"
COPY backend backend
COPY frontend frontend
COPY data data
ENV PORT=8080
CMD exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}
