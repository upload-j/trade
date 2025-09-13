### IB Options MCP Server

Tools to fetch and rank real-time option quotes and greeks from Interactive Brokers (IBKR), accessible via CLI or as an MCP (Model Context Protocol) server for your agent.

---

### What it does
- **get_options_data**: Subscribe to option contracts for a symbol/expiry (calls, puts, or both), gather bid/ask/last, IV, delta/gamma/vega/theta, and spot.
- **rank_options_tool**: Score contracts by simple risk/return metrics (e.g., |Δ|/|Θ|) and return the top N.

Returns clean, per-contract data plus per-contract metrics like `delta_contract` (shares) and `theta_contract` (dollars/day).

---

### Requirements
- **Interactive Brokers** TWS or IB Gateway running locally with API enabled.
  - Default host/port: `127.0.0.1:7497` (TWS) or `127.0.0.1:4001` (Gateway).
  - Enable API: Configure → API → Settings → "Enable ActiveX and Socket Clients".
- **Python 3.10+**
- **Dependencies**:
  - `ib_async` (local vendored copy included; install editable)
  - `mcp` (only if you want to run as MCP server)

Install:
```bash
python3 -m pip install -e ./ib_async
# Only if using MCP
python3 -m pip install mcp
```

Optional environment variables:
- `IB_HOST` (default `127.0.0.1`)
- `IB_PORT` (default `7497`)
- `IB_CLIENT_ID` (default `42`)
- `IB_MD_TYPE` (default `1` real-time; `3` for delayed)

---

### File
- `ib_options_mcp_server.py`

---

### Quick start (CLI)
- Fetch a snapshot for Apple Sep-19-2025 options within ±20% strike window:
```bash
python3 ib_options_mcp_server.py chain \
  --symbol AAPL \
  --expiry 2025-09-19 \
  --right BOTH \
  --window 20 \
  --max-contracts 200
```

- Rank top 10 by |Δ|/|Θ| for calls:
```bash
python3 ib_options_mcp_server.py rank \
  --symbol AAPL \
  --expiry 2025-09-19 \
  --right CALLS \
  --metric delta_per_theta \
  --top 10
```

Supported rights:
- `BOTH`, `CALLS`/`CALL`/`C`, `PUTS`/`PUT`/`P`

Expiry formats:
- `YYYYMMDD` or `YYYY-MM-DD`

Window selection:
- `--window 20.0` selects strikes in ±20% of spot (falls back to full chain if spot is unavailable).

Market data type:
- Use `--md-type 3` for delayed data if you lack real-time options permissions.

---

### Run as MCP server (stdio)
```bash
python3 ib_options_mcp_server.py serve
```
Exposed tools:
- `get_options_data(symbol, expiry, right='BOTH', window=20.0, max_contracts=200, md_type=None)`
- `rank_options_tool(symbol, expiry, right='BOTH', metric='delta_per_theta', top_n=10, window=20.0, max_contracts=200, md_type=None)`

Ask your agent:
- “Check all the options prices for Apple with 2025-09-19 expiration, calculate which has the best risk/return and give me the top 10.”
  - The agent can first call `get_options_data` to retrieve contracts, then `rank_options_tool` with `metric='delta_per_theta'`.

---

### Output schema (contracts)
Each contract includes:
- **Identifiers**: `symbol`, `expiry`, `right`, `strike`, `conId`
- **Prices**: `bid`, `ask`, `last`, `close`, `mid`, `spot`
- **Greeks**: `iv`, `delta`, `gamma`, `vega`, `theta`
- **Per‑contract metrics**: `delta_contract` (shares), `theta_contract` (dollars/day), `score_delta_per_theta`

Example (redacted):
```json
{
  "message": "ok",
  "symbol": "AAPL",
  "expiry": "20250919",
  "right": "BOTH",
  "spot": 210.42,
  "contracts": [
    {
      "symbol": "AAPL",
      "expiry": "20250919",
      "right": "C",
      "strike": 210.0,
      "bid": 16.4,
      "ask": 17.0,
      "mid": 16.7,
      "iv": 0.29,
      "delta": 0.52,
      "gamma": 0.01,
      "vega": 0.11,
      "theta": -0.02,
      "spot": 210.42,
      "conId": 123456789,
      "delta_contract": 52.0,
      "theta_contract": -2.0,
      "score_delta_per_theta": 26.0
    }
  ]
}
```

Notes on units:
- IB greeks are per underlying share.
- We multiply by the option **multiplier** (usually 100) to compute per-contract values:
  - `delta_contract = delta * multiplier` (shares per contract)
  - `theta_contract = theta * multiplier` (dollars/day per contract)

---

### Ranking metrics
- **delta_per_theta**: `abs(delta_contract) / abs(theta_contract)`
- **vega_per_theta**: `abs(vega * 100) / abs(theta_contract)`  (vega scaled by vol point)
- **gamma_per_theta**: `abs(gamma * spot * 0.01 * 100) / abs(theta_contract)` (1% move delta impact per contract over theta)
- **delta_per_premium**: `abs(delta_contract) / mid` (if mid available)

Tip: choose metric per strategy (directional vs vol vs convexity).

---

### Troubleshooting
- **No data/greeks**: Ensure TWS/Gateway is running, API enabled, and IB market data permissions are sufficient. Try delayed data: `--md-type 3` or `IB_MD_TYPE=3`.
- **Expiry not found**: The tool checks available expirations; it will return a list if your requested date isn’t available.
- **Too many contracts**: Use `--window` or `--max-contracts` to limit subscriptions.
- **Connection errors**: Verify `IB_HOST`, `IB_PORT`, and unique `IB_CLIENT_ID`.

---

### Safety
- Read-only: This server requests market data only. It does not place or modify orders.

---

### Examples
- Top 10 AAPL calls by |Δ|/|Θ| for 2025‑09‑19:
```bash
python3 ib_options_mcp_server.py rank --symbol AAPL --expiry 2025-09-19 --right CALLS --metric delta_per_theta --top 10
```
- Fetch both calls and puts in ±10% window:
```bash
python3 ib_options_mcp_server.py chain --symbol AAPL --expiry 2025-09-19 --right BOTH --window 10
```
