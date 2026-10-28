#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONUNBUFFERED=1
mkdir -p data/registry
if ! python3 -c "import fastapi, uvicorn, aiofiles, multipart" 2>/dev/null; then
  python3 -m pip install --break-system-packages -r requirements.txt
fi
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
