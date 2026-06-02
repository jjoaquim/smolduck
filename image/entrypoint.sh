#!/bin/sh
# smolduck VM entrypoint: serve the backend bound to all interfaces so the
# forwarded port reaches it. The workspace is bind-mounted at /workspace and the
# vendored (offline) frontend lives at /app/frontend.
set -e

export SMOLDUCK_WORKSPACE="${SMOLDUCK_WORKSPACE:-/workspace}"
export SMOLDUCK_FRONTEND_DIR="${SMOLDUCK_FRONTEND_DIR:-/app/frontend}"
export PYTHONPATH="/app:${PYTHONPATH}"
# We are inside the sandbox VM: untrusted code (the Python kernel) may run here.
export SMOLDUCK_IN_VM=1
PORT="${SMOLDUCK_PORT:-4290}"

exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
