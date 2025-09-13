#!/usr/bin/env python3
"""
IB Options MCP Server

Tools for:
- Fetching real-time option quotes + greeks for a symbol/expiry (filtered window)
- Ranking contracts by simple risk/return metrics (e.g., |Δ| / |Θ|)

Usage:
- CLI snapshot:
    python ib_options_mcp_server.py chain --symbol AAPL --expiry 20250919 --right BOTH --window 20
    python ib_options_mcp_server.py rank  --symbol AAPL --expiry 20250919 --right CALLS --metric delta_per_theta --top 10
- MCP stdio server (if `mcp` installed):
    python ib_options_mcp_server.py serve

Environment variables:
- IB_HOST (default 127.0.0.1)
- IB_PORT (default 7497 for TWS)
- IB_CLIENT_ID (default 42)
- IB_MD_TYPE (1 real-time, 3 delayed, default 1)

Notes:
- Requires `ib_async` in environment or local editable install: `pip install -e ./ib_async`
- Greeks units: IB greeks are per share; we multiply by contract multiplier (usually 100) to return per-contract dollar/day for theta and per-contract delta in shares.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from dataclasses import dataclass, asdict
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Try local ib_async first (editable install fallback like in greeks_aggregate)
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
LOCAL_IB_ASYNC = os.path.join(PROJECT_ROOT, "ib_async")
if os.path.isdir(LOCAL_IB_ASYNC) and LOCAL_IB_ASYNC not in sys.path:
    sys.path.insert(0, LOCAL_IB_ASYNC)

try:
    from ib_async import IB  # type: ignore
    from ib_async.contract import Stock, Option, Contract  # type: ignore
    from ib_async.objects import OptionComputation  # type: ignore
except Exception as e:
    try:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-e", os.path.join(PROJECT_ROOT, "ib_async")])
        from ib_async import IB  # type: ignore
        from ib_async.contract import Stock, Option, Contract  # type: ignore
        from ib_async.objects import OptionComputation  # type: ignore
    except Exception as e2:
        print("Failed to import ib_async. Install with `pip install ib_async` or keep local ./ib_async.")
        print(f"Import error: {e2}")
        sys.exit(1)

# Optional MCP
try:
    from mcp.server.fastmcp import FastMCP  # type: ignore
    MCP_AVAILABLE = True
except Exception:
    FastMCP = None  # type: ignore
    MCP_AVAILABLE = False

# Allow nested event loops (e.g., when ib_async sync wrappers are called inside async code)
try:
    import nest_asyncio  # type: ignore
    nest_asyncio.apply()  # type: ignore
except Exception:
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_pos_finite(x: Optional[float]) -> bool:
    try:
        v = float(x)
        return math.isfinite(v) and v > 0
    except Exception:
        return False


def _mid(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if _is_pos_finite(bid) and _is_pos_finite(ask):
        return 0.5 * (float(bid) + float(ask))
    return None


IB_SINGLETON: Optional[IB] = None


def get_ib(md_type: Optional[int] = None) -> IB:
    global IB_SINGLETON
    host = os.getenv("IB_HOST", "127.0.0.1")
    port = int(os.getenv("IB_PORT", "7497"))
    client_id = int(os.getenv("IB_CLIENT_ID", "42"))
    md = md_type if md_type is not None else int(os.getenv("IB_MD_TYPE", "1"))

    if IB_SINGLETON is None:
        IB_SINGLETON = IB()
    ib = IB_SINGLETON
    if not ib.isConnected():
        ib.connect(host, port, clientId=client_id)
        ib.reqMarketDataType(md)
    else:
        try:
            ib.reqMarketDataType(md)
        except Exception:
            pass
    return ib


@dataclass
class OptionRow:
    symbol: str
    expiry: str
    right: str
    strike: float
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float]
    close: Optional[float]
    mid: Optional[float]
    iv: Optional[float]
    delta: Optional[float]
    gamma: Optional[float]
    vega: Optional[float]
    theta: Optional[float]
    spot: Optional[float]
    conId: Optional[int]

    # Per-contract metrics
    delta_contract: Optional[float]
    theta_contract: Optional[float]
    score_delta_per_theta: Optional[float]


async def fetch_spot(ib: IB, stock: Contract, timeout_s: float = 3.0) -> Optional[float]:
    t = ib.reqMktData(stock, "", False, False)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        await asyncio.sleep(0.1)
        last = getattr(t, "last", None)
        close = getattr(t, "close", None)
        bid = getattr(t, "bid", None)
        ask = getattr(t, "ask", None)
        if _is_pos_finite(last):
            return float(last)
        if _is_pos_finite(close):
            return float(close)
        if _is_pos_finite(bid) and _is_pos_finite(ask):
            return 0.5 * (float(bid) + float(ask))
    return None


def _normalize_expiry(exp: str) -> str:
    s = exp.strip().replace("-", "")
    if len(s) == 6:  # YYMMDD → YYYYMMDD (assume 20xx)
        return "20" + s
    return s


async def build_option_contracts(ib: IB, symbol: str, expiry: str, right: str, strikes: List[float]) -> List[Contract]:
    contracts: List[Contract] = []
    exp = _normalize_expiry(expiry)
    rights: List[str]
    if right.upper() == "BOTH":
        rights = ["C", "P"]
    elif right.upper() in ("CALL", "CALLS", "C"):
        rights = ["C"]
    else:
        rights = ["P"]
    for r in rights:
        for k in strikes:
            # Use keyword args to avoid positional mismatch (exchange vs multiplier vs currency)
            contracts.append(
                Option(
                    symbol,
                    exp,
                    float(k),
                    r,
                    exchange="SMART",
                    currency="USD",
                )
            )
    qs = ib.qualifyContracts(*contracts) if contracts else []
    # Keep only qualified contracts with a conId
    qualified: List[Contract] = []
    for c in qs:
        try:
            if c is not None and int(getattr(c, "conId", 0)) > 0:
                qualified.append(c)
        except Exception:
            continue
    return qualified


async def fetch_options_data(
    symbol: str,
    expiry: str,
    right: str = "BOTH",
    strikes_window_pct: float = 20.0,
    max_contracts: int = 200,
    md_type: Optional[int] = None,
    timeout_s: float = 6.0,
) -> Dict[str, Any]:
    ib = get_ib(md_type)

    # Qualify stock
    stock = Stock(symbol.upper(), "SMART", "USD")
    stock = ib.qualifyContracts(stock)[0]

    # Spot
    spot = await fetch_spot(ib, stock)

    # Get option params and pick requested expiry
    params = ib.reqSecDefOptParams(stock.symbol, "", stock.secType, stock.conId)
    if not params:
        return {"error": "No option params returned"}
    chain = params[0]
    exp_norm = _normalize_expiry(expiry)
    if exp_norm not in chain.expirations:
        return {"error": f"Expiry {exp_norm} not in available expirations", "expirations": sorted(list(chain.expirations))}

    # Select strikes in window around spot (or all if spot unavailable)
    all_strikes = sorted([float(s) for s in chain.strikes if isinstance(s, (int, float))])
    if spot and _is_pos_finite(spot):
        low = spot * (1.0 - strikes_window_pct / 100.0)
        high = spot * (1.0 + strikes_window_pct / 100.0)
        sel = [k for k in all_strikes if low <= k <= high]
    else:
        sel = all_strikes
    if len(sel) > max_contracts:
        # Thin evenly
        step = max(1, len(sel) // max_contracts)
        sel = sel[::step][:max_contracts]

    # Build and subscribe
    contracts = await build_option_contracts(ib, stock.symbol, exp_norm, right, sel)
    tickers = []
    for c in contracts:
        t = ib.reqMktData(c, "100,101,104,106", False, False)
        tickers.append((c, t))

    # Wait for greeks/quotes
    deadline = time.time() + timeout_s
    await asyncio.sleep(0.2)
    rows: List[OptionRow] = []
    while time.time() < deadline:
        ready = 0
        rows.clear()
        for c, t in tickers:
            mg = getattr(t, "modelGreeks", None) or getattr(t, "lastGreeks", None) or getattr(t, "bidGreeks", None) or getattr(t, "askGreeks", None)
            bid = getattr(t, "bid", None)
            ask = getattr(t, "ask", None)
            last = getattr(t, "last", None)
            close = getattr(t, "close", None)
            iv = getattr(mg, "impliedVol", None) if mg else getattr(t, "impliedVolatility", None)
            delta = getattr(mg, "delta", None) if mg else None
            gamma = getattr(mg, "gamma", None) if mg else None
            vega = getattr(mg, "vega", None) if mg else None
            theta = getattr(mg, "theta", None) if mg else None
            mid = _mid(bid, ask)
            und = getattr(mg, "undPrice", None) if mg else spot
            if mg or (_is_pos_finite(bid) and _is_pos_finite(ask)) or _is_pos_finite(last) or _is_pos_finite(close):
                ready += 1
            # Per-contract metrics (multiply by 100 typical equity options)
            mult = float(getattr(c, "multiplier", 100) or 100)
            delta_contract = float(delta) * mult if delta is not None else None
            theta_contract = float(theta) * mult if theta is not None else None
            score_dpt = None
            if theta_contract is not None and theta_contract != 0 and delta_contract is not None:
                score_dpt = abs(delta_contract) / abs(theta_contract)
            rows.append(
                OptionRow(
                    symbol=stock.symbol,
                    expiry=str(getattr(c, "lastTradeDateOrContractMonth", exp_norm)),
                    right=str(getattr(c, "right", "")),
                    strike=float(getattr(c, "strike", 0.0) or 0.0),
                    bid=float(bid) if _is_pos_finite(bid) else None,
                    ask=float(ask) if _is_pos_finite(ask) else None,
                    last=float(last) if _is_pos_finite(last) else None,
                    close=float(close) if _is_pos_finite(close) else None,
                    mid=float(mid) if _is_pos_finite(mid) else None,
                    iv=float(iv) if _is_pos_finite(iv) else None,
                    delta=float(delta) if delta is not None else None,
                    gamma=float(gamma) if gamma is not None else None,
                    vega=float(vega) if vega is not None else None,
                    theta=float(theta) if theta is not None else None,
                    spot=float(und) if _is_pos_finite(und) else (float(spot) if _is_pos_finite(spot) else None),
                    conId=int(getattr(c, "conId", 0) or 0) or None,
                    delta_contract=delta_contract,
                    theta_contract=theta_contract,
                    score_delta_per_theta=score_dpt,
                )
            )
        if ready >= max(5, len(tickers) // 3):
            break
        await asyncio.sleep(0.2)

    return {
        "message": "ok",
        "generated_at": _now_iso(),
        "symbol": stock.symbol,
        "expiry": _normalize_expiry(expiry),
        "right": right,
        "md_type": int(os.getenv("IB_MD_TYPE", "1")) if md_type is None else md_type,
        "spot": float(spot) if _is_pos_finite(spot) else None,
        "contracts": [asdict(r) for r in rows],
    }


def _rank(rows: List[OptionRow], metric: str, top_n: int) -> List[Dict[str, Any]]:
    def score(row: OptionRow) -> float:
        if metric == "delta_per_theta":
            if row.theta_contract is None or row.theta_contract == 0 or row.delta_contract is None:
                return -1e18
            return abs(row.delta_contract) / abs(row.theta_contract)
        if metric == "vega_per_theta":
            if row.theta_contract is None or row.theta_contract == 0 or row.vega is None:
                return -1e18
            return abs(row.vega * 100.0) / abs(row.theta_contract)
        if metric == "gamma_per_theta":
            if row.theta_contract is None or row.theta_contract == 0 or row.gamma is None or row.spot is None:
                return -1e18
            # normalize gamma to 1% move impact in delta shares per contract
            gamma_delta_1pct_contract = (row.gamma or 0.0) * (row.spot * 0.01) * 100.0
            return abs(gamma_delta_1pct_contract) / abs(row.theta_contract)
        # default: highest mid/price efficiency: delta per $ premium
        if row.mid is None or row.mid <= 0 or row.delta_contract is None:
            return -1e18
        return abs(row.delta_contract) / float(row.mid)

    rows_scored = sorted(rows, key=score, reverse=True)
    out: List[Dict[str, Any]] = []
    for r in rows_scored[: max(1, top_n)]:
        d = asdict(r)
        d["score"] = score(r)
        out.append(d)
    return out


async def rank_options(
    symbol: str,
    expiry: str,
    right: str = "BOTH",
    strikes_window_pct: float = 20.0,
    max_contracts: int = 200,
    metric: str = "delta_per_theta",
    top_n: int = 10,
    md_type: Optional[int] = None,
) -> Dict[str, Any]:
    data = await fetch_options_data(
        symbol=symbol,
        expiry=expiry,
        right=right,
        strikes_window_pct=strikes_window_pct,
        max_contracts=max_contracts,
        md_type=md_type,
    )
    if "contracts" not in data:
        return data
    rows = [OptionRow(**c) for c in data["contracts"]]
    ranked = _rank(rows, metric, top_n)
    return {
        "message": "ok",
        "generated_at": _now_iso(),
        "symbol": data.get("symbol"),
        "expiry": data.get("expiry"),
        "right": right,
        "metric": metric,
        "top": ranked,
    }


# Optional MCP wrapper
mcp: Any = None
if MCP_AVAILABLE:
    mcp = FastMCP("IB Options MCP Server", instructions=(
        "Tools: get_options_data(symbol, expiry, right, window, max_contracts, md_type), "
        "rank_options(symbol, expiry, right, window, max_contracts, metric, top_n, md_type). "
        "Returns live quotes and greeks per contract and simple risk/return rankings."
    ))  # type: ignore

    @mcp.tool()  # type: ignore
    async def get_options_data(symbol: str, expiry: str, right: str = "BOTH", window: float = 20.0, max_contracts: int = 200, md_type: Optional[int] = None) -> Dict[str, Any]:
        return await fetch_options_data(symbol=symbol, expiry=expiry, right=right, strikes_window_pct=window, max_contracts=max_contracts, md_type=md_type)

    @mcp.tool()  # type: ignore
    async def rank_options_tool(symbol: str, expiry: str, right: str = "BOTH", metric: str = "delta_per_theta", top_n: int = 10, window: float = 20.0, max_contracts: int = 200, md_type: Optional[int] = None) -> Dict[str, Any]:
        return await rank_options(symbol=symbol, expiry=expiry, right=right, strikes_window_pct=window, max_contracts=max_contracts, metric=metric, top_n=top_n, md_type=md_type)


# CLI
def _cli() -> int:
    parser = argparse.ArgumentParser(description="IB Options MCP Server")
    sub = parser.add_subparsers(dest="cmd")

    p_chain = sub.add_parser("chain", help="Fetch options chain snapshot")
    p_chain.add_argument("--symbol", required=True)
    p_chain.add_argument("--expiry", required=True, help="YYYYMMDD or YYYY-MM-DD")
    p_chain.add_argument("--right", default="BOTH", choices=["BOTH", "CALLS", "PUTS", "CALL", "PUT", "C", "P"])
    p_chain.add_argument("--window", type=float, default=20.0, help="Strike window ±pct around spot")
    p_chain.add_argument("--max-contracts", type=int, default=200)
    p_chain.add_argument("--md-type", type=int, default=None)

    p_rank = sub.add_parser("rank", help="Rank options by metric")
    p_rank.add_argument("--symbol", required=True)
    p_rank.add_argument("--expiry", required=True)
    p_rank.add_argument("--right", default="BOTH", choices=["BOTH", "CALLS", "PUTS", "CALL", "PUT", "C", "P"])
    p_rank.add_argument("--window", type=float, default=20.0)
    p_rank.add_argument("--max-contracts", type=int, default=200)
    p_rank.add_argument("--metric", default="delta_per_theta", choices=["delta_per_theta", "vega_per_theta", "gamma_per_theta", "delta_per_premium"])
    p_rank.add_argument("--top", type=int, default=10)
    p_rank.add_argument("--md-type", type=int, default=None)

    if MCP_AVAILABLE:
        sub.add_parser("serve", help="Run MCP server (stdio)")

    args = parser.parse_args()

    if args.cmd == "chain":
        ib = get_ib(args.md_type)
        try:
            import asyncio
            print(asyncio.run(fetch_options_data(args.symbol, args.expiry, args.right, args.window, args.max_contracts, args.md_type)))
        finally:
            ib.disconnect()
        return 0

    if args.cmd == "rank":
        ib = get_ib(args.md_type)
        try:
            import asyncio
            print(asyncio.run(rank_options(args.symbol, args.expiry, args.right, args.window, args.max_contracts, args.metric, args.top, args.md_type)))
        finally:
            ib.disconnect()
        return 0

    if MCP_AVAILABLE and args.cmd == "serve":
        import asyncio
        print("🚀 IB Options MCP Server (stdio)")
        asyncio.run(mcp.run_stdio_async())  # type: ignore
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
