#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

VENV_DIR=".venv"
PY="python3"

if [[ ! -d "$VENV_DIR" ]]; then
  "$PY" -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install -U pip
# Prefer local ib_async if present; fall back to PyPI
if [[ -d "ib_async" ]]; then
  "$VENV_DIR/bin/pip" install -e ./ib_async
fi
"$VENV_DIR/bin/pip" install -r requirements_ib_options_mcp.txt

# Allow user to set IB_MD_TYPE=3 for delayed
: "${IB_MD_TYPE:=1}"

exec "$VENV_DIR/bin/python" "$(pwd)/ib_options_mcp_server.py" serve
