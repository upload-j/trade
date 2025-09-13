# Real‑Time Greeks Dashboard

Lightweight, local dashboard for monitoring portfolio Greeks and risk in real time.

Run `python dashboard.py` and it will install dependencies (if missing), start the aggregator, serve the dashboard, and open your browser. The UI auto‑refreshes on a timer.

## How To Run

- Quick start: `python dashboard.py`
- Requirements are installed automatically (`ib_async`, `scipy`, `numpy`).
- Optional env vars:
  - Connection: `IB_HOST`, `IB_PORT`, `IB_CLIENT_ID`, `IB_ACCOUNTS`
  - Timing: `GREEKS_INTERVAL` (default `2`), `GREEKS_HTTP_PORT` (default `8765` preferred)

## First‑Time Interactive Brokers Setup

To let the aggregator connect to IBKR, enable the API in TWS or IB Gateway and set host/port.

- Install and start one of:
  - Trader Workstation (TWS), or
  - IB Gateway
- Log in (paper or live) and leave it running.
- Enable API access:
  - TWS: Configure → API → Settings
    - Check “Enable ActiveX and Socket Clients”
    - Socket port: 7497 (paper) or 7496 (live) is common
    - Trusted IPs: add `127.0.0.1` (localhost). If connecting from another machine on your LAN, add that LAN IP too.
  - IB Gateway: Settings → API → Same options as above
- Firewall: allow inbound connections to the chosen port on localhost.

Configure the app to match your TWS/Gateway host/port:

```bash
export IB_HOST=127.0.0.1      # or the LAN IP of the machine running TWS/Gateway
export IB_PORT=7497           # 7497 (paper), 7496 (live) typical
export IB_CLIENT_ID=1         # any positive int; must be unique per client

python dashboard.py
```

Notes
- If you don’t have real‑time market data, set `IB_MD_TYPE=3` for delayed quotes (when using the lower‑level script in `greeks_aggregate`).
- You can also pass accounts with `IB_ACCOUNTS="U1234567,U7654321"` to filter positions.
- If you see “Failed to connect to IBKR”, re‑check API enabled, host/port, login status, and Trusted IPs.

## Project Structure (what each file does)

- `dashboard.py`
  - Single entrypoint. Ensures Python deps, launches the aggregator in latest‑only mode, starts a local CORS‑enabled HTTP server, and opens the dashboard page.
  - Points the dashboard at `greeks_aggregate/latest_data.jsonl`.

- `greeks_aggregate/greeks_aggregate.py`
  - Aggregator engine. Connects to IB via `ib_async`, calculates Greeks (Black‑Scholes fallback), aggregates by underlying and portfolio, and emits JSONL rows for `underlying`, `option`, `stock`, `portfolio`, and `risk_assessment` scopes.
  - Outputs latest‑only (`latest_data.jsonl`) and optional timeseries (`greeks_timeseries.jsonl`).

- `greeks_aggregate/greeks_dashboard.html`
  - Static dashboard UI. Fetches a JSONL file (default `latest_data.jsonl`) and renders Overview, Composition, and Options tabs. Optional charts via Chart.js; auto‑refresh interval selectable.

- `greeks_aggregate/start_greeks_server.command`
  - macOS one‑click launcher. Auto‑creates `.venv`, ensures deps, runs the aggregator with latest‑only output, and opens the dashboard.

- Data files
  - `greeks_aggregate/latest_data.jsonl` — latest‑only data; created/overwritten at runtime.
  - `greeks_aggregate/greeks_timeseries.jsonl` — optional append‑only history (disabled by default).
  - `greeks_aggregate/data/greeks_timeseries.jsonl` — sample dataset for offline demo.

## Minimal Keep Set

- For “viewer + live data”: `dashboard.py`, `greeks_aggregate/greeks_aggregate.py`, `greeks_aggregate/greeks_dashboard.html`.
- For “viewer only” with static data: keep `greeks_aggregate/greeks_dashboard.html` and provide a small JSONL file.
