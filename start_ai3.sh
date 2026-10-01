#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")" && pwd)"
port="${AI3_PORT:-8030}"

cd "$project_dir"
exec conda run -n fault --no-capture-output \
  python -m uvicorn web.api.main:app --host 127.0.0.1 --port "$port"

