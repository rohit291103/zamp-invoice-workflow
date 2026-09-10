#!/usr/bin/env bash
# Start the AP invoice process on http://127.0.0.1:8077
set -euo pipefail
cd "$(dirname "$0")"
exec ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8077 "$@"
