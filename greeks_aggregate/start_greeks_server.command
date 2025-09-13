#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

# User-configurable defaults
HOST=${IB_HOST:-127.0.0.1}
PORT=${IB_PORT:-7497}
CLIENT_ID=${IB_CLIENT_ID:-3}
ACCOUNTS=${IB_ACCOUNTS:-}
INTERVAL=${GREEKS_INTERVAL:-2}
OUTFILE=${GREEKS_JSON:-greeks_timeseries.jsonl}
LATEST_FILE=${GREEKS_LATEST_JSON:-latest_data.jsonl}
NO_TIMESERIES=${GREEKS_NO_TIMESERIES:-1}
# Default to real-time data (1); override with IB_MD_TYPE=3 for delayed
MD_TYPE=${IB_MD_TYPE:-1}
HTTP_PORT=${GREEKS_HTTP_PORT:-8765}

# Ensure local virtualenv with required deps (auto-bootstrap)
VENV_DIR=${VENV_DIR:-.venv}
if [[ ! -d "$VENV_DIR" ]]; then
  echo "Creating virtualenv at $VENV_DIR" >&2
  python3 -m venv "$VENV_DIR"
fi
PY="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"
"$PIP" install -U pip >/dev/null 2>&1 || true

# Install deps if missing
if ! "$PY" - <<'PY' >/dev/null 2>&1
import importlib
for m in ("ib_async","scipy","numpy"):
    importlib.import_module(m)
print("ok")
PY
then
  echo "Installing Python deps (ib_async, scipy, numpy) into $VENV_DIR" >&2
  "$PIP" install -U ib_async scipy numpy
fi

ARGS=(
  --host "$HOST"
  --port "$PORT"
  --client-id "$CLIENT_ID"
  --interval "$INTERVAL"
  --outfile "$OUTFILE"
  --latest-file "$LATEST_FILE"
  --md-type "$MD_TYPE"
  --serve
  --http-port "$HTTP_PORT"
  --debug
  --print
)

if [[ -n "$ACCOUNTS" ]]; then
  ARGS+=(--accounts "$ACCOUNTS")
fi

# Default to latest-only unless user overrides NO_TIMESERIES=0
if [[ "$NO_TIMESERIES" != "0" ]]; then
  ARGS+=(--no-timeseries)
fi

# Start the aggregator in background with built-in HTTP server
"$PY" "$(pwd)/greeks_aggregate.py" "${ARGS[@]}" &
PID=$!
sleep 1

# Open the dashboard pointing at the latest-only JSONL
URL="http://127.0.0.1:${HTTP_PORT}/greeks_dashboard.html?file=$("$PY" -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))' "$LATEST_FILE")"
open "$URL" || true

echo "Greeks server running (PID $PID). Press Ctrl+C to stop."
wait "$PID"
