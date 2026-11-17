#!/usr/bin/env bash
# Loads the EPA Smart Location Database v3 (Jan 2021, CC0) for the five NYC counties into
# BigQuery as epa_sld.smart_location_nyc. Safe to re-run (the table is replaced).
#
#   bash sql/epa/load.sh [project-id]
#
# Downloads a 202 MB CSV from edg.epa.gov into a temp folder, keeps NYC (6,493 block
# groups), rebuilds GEOID10 (the CSV stores it in scientific notation, losing digits) and
# turns -99999 sentinels into NULL. Then rebuild the grid: bash sql/grid/build.sh
set -euo pipefail

PROJECT="${1:-$(gcloud config get-value project 2>/dev/null)}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
URL="https://edg.epa.gov/EPADataCommons/public/OA/EPA_SmartLocationDatabase_V3_Jan_2021_Final.csv"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

echo "== downloading $(basename "${URL}")"
curl -fsSL -o "${WORK}/sld.csv" "${URL}"

echo "== filtering to NYC"
python3 -I "${DIR}/filter_sld.py" "${WORK}/sld.csv" "${WORK}/sld_nyc.csv"

SCHEMA=$(head -1 "${WORK}/sld_nyc.csv" | tr ',' '\n' \
  | awk '{ if ($1=="bg_geo_id"||$1=="county_fips") print $1":STRING"; else print $1":FLOAT64" }' \
  | paste -sd, -)

bq --project_id="${PROJECT}" mk --dataset --location=US "${PROJECT}:epa_sld" 2>/dev/null || true
echo "== loading ${PROJECT}.epa_sld.smart_location_nyc"
bq --project_id="${PROJECT}" load --source_format=CSV --skip_leading_rows=1 --replace \
  "${PROJECT}:epa_sld.smart_location_nyc" "${WORK}/sld_nyc.csv" "${SCHEMA}"
echo "Done."
