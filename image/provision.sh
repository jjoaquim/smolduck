#!/bin/sh
# Runs INSIDE the builder VM (python:3.12-slim) with the repo mounted at /src:ro
# and network enabled. Bakes the backend, the offline frontend, and the DuckDB
# extensions into /app so the packed VM needs no network at runtime.
set -e

echo "[provision] installing backend deps"
pip install --no-cache-dir --root-user-action=ignore \
  "duckdb==1.5.3" "fastapi" "uvicorn[standard]" "polars" "pandas" "pyarrow" \
  "plotly" "scikit-learn" "anthropic"

echo "[provision] copying backend app -> /app/app"
mkdir -p /app
rm -rf /app/app
cp -r /src/backend/app /app/app

echo "[provision] vendoring frontend assets (offline) -> /app/frontend"
python3 /src/image/vendor_assets.py --src-frontend /src/frontend --out /app/frontend

echo "[provision] pre-installing DuckDB extensions (httpfs, excel)"
python3 -c "import duckdb; c=duckdb.connect(); c.execute('INSTALL httpfs; INSTALL excel;'); print('  extensions installed')"

echo "[provision] installing entrypoint"
cp /src/image/entrypoint.sh /app/entrypoint.sh
chmod +x /app/entrypoint.sh

echo "[provision] done"
