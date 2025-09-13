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
# Default to real-time data (1); override with IB_MD_TYPE=3 for delayed
MD_TYPE=${IB_MD_TYPE:-1}
HTTP_PORT=${GREEKS_HTTP_PORT:-8765}

ARGS=(
  --host "$HOST"
  --port "$PORT"
  --client-id "$CLIENT_ID"
  --interval "$INTERVAL"
  --outfile "$OUTFILE"
  --md-type "$MD_TYPE"
  --serve
  --http-port "$HTTP_PORT"
  --debug
  --print
)

if [[ -n "$ACCOUNTS" ]]; then
  ARGS+=(--accounts "$ACCOUNTS")
fi

# Start the aggregator in background with built-in HTTP server
/usr/bin/env python3 "$(pwd)/greeks_aggregate.py" "${ARGS[@]}" &
PID=$!
sleep 1

# Open the dashboard pointing at the served JSONL
URL="http://127.0.0.1:${HTTP_PORT}/greeks_dashboard.html?file=$(python3 -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))' "$OUTFILE")"
open "$URL" || true

echo "Greeks server running (PID $PID). Press Ctrl+C to stop."
wait "$PID"
