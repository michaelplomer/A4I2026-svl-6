#!/usr/bin/env bash
# Deploy MCP Toolbox for Databases (agent/toolbox/tools.yaml) to Cloud Run, private, 1 warm instance.
# Run from the repo root in Cloud Shell:
#     bash agent/toolbox/deploy.sh
# Prints the TOOLBOX_URL to set on the portal / agent. Safe to re-run (adds a new secret version).
set -euo pipefail

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-heatrx-toolbox}"
SA_NAME="${SA_NAME:-heatrx-toolbox}"
SA="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
IMAGE="${TOOLBOX_IMAGE:-us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:latest}"
# Service account of the portal / agent that will CALL the toolbox (Cloud Run default if unset).
CALLER_SA="${CALLER_SA:-$(gcloud iam service-accounts list --project "$PROJECT" \
  --filter='email~compute@developer.gserviceaccount.com' --format='value(email)' | head -1)}"
here="$(cd "$(dirname "$0")" && pwd)"

echo "▶ Project ${PROJECT}, region ${REGION}"
gcloud services enable run.googleapis.com secretmanager.googleapis.com bigquery.googleapis.com --project "$PROJECT"

echo "▶ Service account ${SA} (BigQuery read + jobs)"
gcloud iam service-accounts describe "$SA" --project "$PROJECT" >/dev/null 2>&1 \
  || gcloud iam service-accounts create "$SA_NAME" --display-name "HeatRx MCP Toolbox" --project "$PROJECT"
for ROLE in roles/bigquery.dataViewer roles/bigquery.jobUser roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:${SA}" --role "$ROLE" \
    --condition=None --quiet >/dev/null
done

echo "▶ tools.yaml -> Secret Manager (heatrx-tools)"
sed "s/__PROJECT__/${PROJECT}/g" "$here/tools.yaml" > /tmp/heatrx-tools.yaml
if gcloud secrets describe heatrx-tools --project "$PROJECT" >/dev/null 2>&1; then
  gcloud secrets versions add heatrx-tools --data-file=/tmp/heatrx-tools.yaml --project "$PROJECT"
else
  gcloud secrets create heatrx-tools --data-file=/tmp/heatrx-tools.yaml --project "$PROJECT"
fi

echo "▶ Cloud Run ${SERVICE} (min-instances=1, private)"
gcloud run deploy "$SERVICE" --image "$IMAGE" --region "$REGION" --project "$PROJECT" \
  --service-account "$SA" --set-secrets "/app/tools.yaml=heatrx-tools:latest" \
  --args="--tools-file=/app/tools.yaml","--address=0.0.0.0","--port=8080" \
  --min-instances=1 --no-allow-unauthenticated --quiet

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format='value(status.url)')"
if [[ -n "$CALLER_SA" ]]; then
  echo "▶ Allow ${CALLER_SA} to invoke the toolbox"
  gcloud run services add-iam-policy-binding "$SERVICE" --region "$REGION" --project "$PROJECT" \
    --member "serviceAccount:${CALLER_SA}" --role roles/run.invoker --quiet >/dev/null
fi

cat <<EOF

✅ MCP Toolbox is up: ${URL}  (MCP endpoint ${URL}/mcp/heatrx)

Local (adk web / portal):   export TOOLBOX_URL=${URL}
Portal on Cloud Run:        gcloud run services update heat-portal --region ${REGION} \\
                              --update-env-vars TOOLBOX_URL=${URL}
Quick check (lists tools):  curl -s -H "Authorization: Bearer \$(gcloud auth print-identity-token)" ${URL}/api/toolset/heatrx | head -c 600
EOF
