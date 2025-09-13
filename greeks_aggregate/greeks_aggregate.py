#!/usr/bin/env python3
import os
import sys
import json
import argparse
import time
import math
from datetime import datetime, timezone
from typing import Dict, DefaultDict, Tuple, List, Optional
from collections import defaultdict
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import socket
import re
from scipy.stats import norm
# --- US Eastern market time helpers (no external tz deps) ---
def _second_sunday_in_march(year: int) -> datetime:
    # Find first day of March
    d = datetime(year, 3, 1)
    # Python: Monday=0 .. Sunday=6; we want Sunday(6)
    first_sunday_offset = (6 - d.weekday()) % 7
    first_sunday = d.replace(day=1 + first_sunday_offset)
    second_sunday = first_sunday.replace(day=first_sunday.day + 7)
    return second_sunday


def _first_sunday_in_november(year: int) -> datetime:
    d = datetime(year, 11, 1)
    first_sunday_offset = (6 - d.weekday()) % 7
    first_sunday = d.replace(day=1 + first_sunday_offset)
    return first_sunday


def _is_us_eastern_dst(d: datetime) -> bool:
    # DST between second Sunday in March and first Sunday in November
    start = _second_sunday_in_march(d.year)
    end = _first_sunday_in_november(d.year)
    return d.date() >= start.date() and d.date() < end.date()


def expiry_in_utc_for_us_equity_options(expiry_calendar_date: datetime) -> datetime:
    """Given an option expiry calendar date (naive), return the UTC datetime corresponding
    to 4:00 PM US Eastern on that date, accounting for DST without external tz libs.
    """
    # 4:00 PM ET corresponds to 20:00 UTC during DST, else 21:00 UTC
    utc_hour = 20 if _is_us_eastern_dst(expiry_calendar_date) else 21
    return datetime(expiry_calendar_date.year, expiry_calendar_date.month, expiry_calendar_date.day, utc_hour, 0, 0, tzinfo=timezone.utc)


# Beta coefficients vs SPY for risk calculations
DEFAULT_BETAS = {
    'NVDA': 1.8, 'PLTR': 2.2, 'META': 1.3, 'TSLA': 2.0, 'AMZN': 1.4,
    'XYZ': 1.0, 'SPY': 1.0, 'GLD': -0.1, 'BLK': 1.5, 'SNOW': 1.8,
    'QQQ': 1.0, 'IWM': 1.2, 'VTI': 1.0
}

# Optional live betas fetched from IB fundamentals (generic tick 258)
RUNTIME_BETAS: Dict[str, float] = {}

# Sector classifications for risk analysis
SECTORS = {
    'NVDA': 'Technology', 'PLTR': 'Technology', 'META': 'Technology',
    'TSLA': 'Consumer Discretionary', 'AMZN': 'Consumer Discretionary',
    'XYZ': 'Unknown', 'SPY': 'Broad Market', 'GLD': 'Commodities',
    'BLK': 'Financials', 'SNOW': 'Technology', 'QQQ': 'Technology ETF',
    'IWM': 'Small Cap ETF', 'VTI': 'Broad Market ETF'
}

# Make local ib_async package importable if running from repo root
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
LOCAL_IB_ASYNC_PARENT = os.path.join(PROJECT_ROOT, "ib_async")
if os.path.isdir(LOCAL_IB_ASYNC_PARENT) and LOCAL_IB_ASYNC_PARENT not in sys.path:
    sys.path.insert(0, LOCAL_IB_ASYNC_PARENT)

try:
    from ib_async import IB, util
    from ib_async.contract import Contract, Stock
    from ib_async.objects import Position, OptionComputation
except Exception as e:
    # Attempt local editable install if the source tree is present
    pyproject_path = os.path.join(PROJECT_ROOT, "ib_async", "pyproject.toml")
    if os.path.isfile(pyproject_path):
        print("ib_async import failed; attempting local editable install (pip install -e ./ib_async)...")
        import subprocess
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-e", os.path.join(PROJECT_ROOT, "ib_async")])
            from ib_async import IB, util
            from ib_async.contract import Contract, Stock
            from ib_async.objects import Position, OptionComputation
        except Exception as e2:
            print("Import still failing after editable install.")
            print(f"Import error: {e2}")
            sys.exit(1)
    else:
        print("Failed to import ib_async. Try: pip install ib_async")
        print(f"Import error: {e}")
        sys.exit(1)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Aggregate and stream portfolio Greeks to JSON Lines")
    p.add_argument("--host", default=os.getenv("IB_HOST", "127.0.0.1"), help="IBKR host (default: 127.0.0.1 or $IB_HOST)")
    p.add_argument("--port", type=int, default=int(os.getenv("IB_PORT", "7497")), help="IBKR port (default: 7497 or $IB_PORT)")
    p.add_argument("--client-id", type=int, default=int(os.getenv("IB_CLIENT_ID", "1")), help="Unique client id (default: 1 or $IB_CLIENT_ID)")
    p.add_argument("--accounts", default=os.getenv("IB_ACCOUNTS", ""), help="Comma-separated account ids to include (default: all)")
    p.add_argument("--account", default=os.getenv("IB_ACCOUNT", ""), help="Single account to include (shortcut for --accounts)")
    p.add_argument("--outfile", default=os.getenv("GREEKS_JSON", os.path.join(PROJECT_ROOT, "greeks_timeseries.jsonl")), help="Output JSONL file path")
    p.add_argument("--interval", type=float, default=float(os.getenv("GREEKS_INTERVAL", "2")), help="Snapshot interval seconds (default: 2)")
    p.add_argument("--print", dest="do_print", action="store_true", help="Also print a human-readable summary to console each snapshot")
    p.add_argument("--once", action="store_true", help="Take a single snapshot, write/print it, and exit")
    p.add_argument("--warmup", type=float, default=float(os.getenv("GREEKS_WARMUP", "0")), help="Warm-up seconds before first snapshot (useful with --once)")
    p.add_argument("--md-type", type=int, default=int(os.getenv("IB_MD_TYPE", "1")), choices=[1, 2, 3, 4], help="Market data type: 1=RealTime, 2=Frozen, 3=Delayed, 4=DelayedFrozen")
    p.add_argument("--serve", action="store_true", help="Also serve the output directory over HTTP with CORS enabled")
    p.add_argument("--http-port", type=int, default=int(os.getenv("GREEKS_HTTP_PORT", "8765")), help="HTTP server port when --serve is used (default: 8765)")
    p.add_argument("--debug", action="store_true", help="Print debug info about option subscriptions/greeks readiness")
    p.add_argument("--cash-currencies", default=os.getenv("GREEKS_CASH_CCYS", ""), help="Comma-separated cash currency whitelist (e.g., USD,EUR). Empty = include all.")
    p.add_argument("--fetch-beta", action="store_true", default=bool(int(os.getenv("GREEKS_FETCH_BETA", "0"))), help="Fetch Beta from IB fundamentals (generic tick 258) for underlyings")
    return p.parse_args()


def multiplier_of(contract: Contract) -> float:
    m = getattr(contract, "multiplier", None)
    if not m:
        # heuristic defaults
        if getattr(contract, "secType", "") in ("OPT", "FOP"):
            return 100.0
        return 1.0
    try:
        return float(m)
    except Exception:
        return 1.0


def find_best_greeks(ticker) -> OptionComputation | None:
    # Prefer modelGreeks, else lastGreeks, then bid/ask greeks
    for attr in ("modelGreeks", "lastGreeks", "bidGreeks", "askGreeks"):
        g = getattr(ticker, attr, None)
        if g is not None:
            return g
    return None


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def calculate_bs_greeks(S, K, T, r, sigma, option_type='C'):
    """Calculate Black-Scholes Greeks and price when IB doesn't provide them.

    Returns an object with fields: delta, gamma, vega, theta (per day), undPrice,
    price (per underlying unit), d1, d2, impliedVol.
    """
    if T <= 0 or sigma <= 0 or S <= 0:
        return None

    try:
        sqrtT = math.sqrt(T)
        d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
        d2 = d1 - sigma * sqrtT

        if option_type.upper() == 'C':
            delta = norm.cdf(d1)
            # Theta here is annual; convert to per-day later
            theta = -S * norm.pdf(d1) * sigma / (2 * sqrtT) - r * K * math.exp(-r * T) * norm.cdf(d2)
            price = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
        else:  # Put
            delta = -norm.cdf(-d1)
            theta = -S * norm.pdf(d1) * sigma / (2 * sqrtT) + r * K * math.exp(-r * T) * norm.cdf(-d2)
            price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)

        gamma = norm.pdf(d1) / (S * sigma * sqrtT)
        # Vega per 1 vol point (1.00 = 100%) consistent with IB style
        vega = S * norm.pdf(d1) * sqrtT / 100.0

        class BSGreeks:
            def __init__(self):
                self.delta = delta
                self.gamma = gamma
                self.vega = vega
                # IB reports theta per day; convert from annual to daily
                self.theta = theta / 365.0
                self.undPrice = S
                self.price = price
                self.d1 = d1
                self.d2 = d2
                self.impliedVol = sigma

        return BSGreeks()
    except Exception:
        return None


def is_positive_finite(value: Optional[float]) -> bool:
    """True if value is a finite number > 0 (filters out None/NaN/inf)."""
    try:
        v = float(value)
        return math.isfinite(v) and v > 0.0
    except Exception:
        return False


def bs_price(S: float, K: float, T: float, r: float, sigma: float, option_type: str) -> Optional[float]:
    """Return Black-Scholes price for given sigma or None if invalid."""
    g = calculate_bs_greeks(S, K, T, r, sigma, option_type)
    return getattr(g, 'price', None) if g is not None else None


def implied_vol_from_price(S: float, K: float, T: float, r: float, price: float, option_type: str) -> Optional[float]:
    """Estimate implied volatility via bisection. Returns sigma or None.

    - Bounds: [1e-6, 5.0]
    - Tolerance: 1e-6 on price difference or 1e-6 on sigma width
    """
    try:
        if not (is_positive_finite(S) and is_positive_finite(K) and T > 0 and is_positive_finite(price)):
            return None
        lo, hi = 1e-6, 5.0
        plo = bs_price(S, K, T, r, lo, option_type)
        phi = bs_price(S, K, T, r, hi, option_type)
        if plo is None or phi is None:
            return None
        # If target price outside achievable range, clamp
        if price <= plo:
            return lo
        if price >= phi:
            return hi
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            pm = bs_price(S, K, T, r, mid, option_type)
            if pm is None:
                return None
            if abs(pm - price) < 1e-6 or (hi - lo) < 1e-6:
                return mid
            if pm > price:
                hi = mid
            else:
                lo = mid
        return 0.5 * (lo + hi)
    except Exception:
        return None

def calculate_beta_weighted_greeks(positions: List[Dict], betas: Optional[Dict[str, float]] = None) -> Dict:
    """Calculate beta-weighted Greeks for all positions"""
    beta_weighted = {'delta': 0.0, 'gamma': 0.0, 'vega': 0.0, 'theta': 0.0, 'positions': []}
    
    for pos in positions:
        symbol = pos.get('symbol', '')
        beta = None
        if betas:
            beta = betas.get(symbol)
        if beta is None:
            beta = DEFAULT_BETAS.get(symbol, 1.0)
        
        bw_delta = pos.get('delta', 0) * beta
        bw_gamma = pos.get('gamma', 0) * beta
        bw_vega = pos.get('vega', 0) * beta
        bw_theta = pos.get('theta', 0) * beta
        
        beta_weighted['delta'] += bw_delta
        beta_weighted['gamma'] += bw_gamma
        beta_weighted['vega'] += bw_vega
        beta_weighted['theta'] += bw_theta
        
        beta_weighted['positions'].append({**pos, 'beta': beta, 'beta_weighted_delta': bw_delta})
    
    return beta_weighted


def analyze_concentration(beta_weighted_positions: List[Dict]) -> Dict:
    """Analyze concentration risk by symbol and sector"""
    total_bw_delta = sum(abs(pos['beta_weighted_delta']) for pos in beta_weighted_positions)
    
    # By symbol
    symbol_exposure = defaultdict(float)
    for pos in beta_weighted_positions:
        symbol_exposure[pos['symbol']] += abs(pos['beta_weighted_delta'])
    
    symbol_concentration = {
        symbol: (exposure / total_bw_delta * 100) if total_bw_delta else 0
        for symbol, exposure in symbol_exposure.items()
    }
    
    # By sector  
    sector_exposure = defaultdict(float)
    for pos in beta_weighted_positions:
        sector = SECTORS.get(pos['symbol'], 'Unknown')
        sector_exposure[sector] += abs(pos['beta_weighted_delta'])
    
    sector_concentration = {
        sector: (exposure / total_bw_delta * 100) if total_bw_delta else 0
        for sector, exposure in sector_exposure.items()
    }
    
    return {
        'total_beta_weighted_delta': total_bw_delta,
        'by_symbol': dict(sorted(symbol_concentration.items(), key=lambda x: x[1], reverse=True)),
        'by_sector': dict(sorted(sector_concentration.items(), key=lambda x: x[1], reverse=True)),
        'herfindahl_index': sum(x**2 for x in symbol_concentration.values()) / 10000
    }


def calculate_stress_scenarios(beta_weighted_delta: float, total_vega: float) -> Dict:
    """Calculate P&L under various stress scenarios"""
    scenarios = {
        'market_correction_10': {'spy_move': -10.0, 'vix_change': 0.5, 'description': 'Market correction (-10% SPY, VIX +50%)'},
        'market_rally_5': {'spy_move': 5.0, 'vix_change': -0.2, 'description': 'Market rally (+5% SPY, VIX -20%)'},
        'volatility_crush': {'spy_move': 0.0, 'vix_change': -0.3, 'description': 'Volatility crush (flat market, VIX -30%)'},
        'volatility_spike': {'spy_move': 0.0, 'vix_change': 0.8, 'description': 'Volatility spike (flat market, VIX +80%)'}
    }
    
    results = {}
    for scenario_name, params in scenarios.items():
        spy_dollar_move = params['spy_move'] * 6.37  # Assume SPY ~$637
        delta_pnl = beta_weighted_delta * spy_dollar_move / 100  # Convert % to dollar move
        vega_pnl = total_vega * params['vix_change']
        
        results[scenario_name] = {
            'description': params['description'],
            'delta_pnl': delta_pnl,
            'vega_pnl': vega_pnl,
            'total_pnl': delta_pnl + vega_pnl
        }
    
    return results


def generate_risk_summary(underlying_positions: List[Dict], option_positions: List[Dict], betas: Optional[Dict[str, float]] = None) -> Dict:
    """Generate comprehensive risk assessment"""
    all_positions = []
    
    # Add underlying positions (stocks/ETFs)
    for pos in underlying_positions:
        if pos.get('delta_shares', 0) != 0:
            all_positions.append({
                'symbol': pos['symbol'], 'delta': pos.get('delta_shares', 0),
                'gamma': 0, 'vega': 0, 'theta': 0, 'type': 'stock'
            })
    
    # Add option positions
    for pos in option_positions:
        all_positions.append({**pos, 'type': 'option'})
    
    # Calculate metrics
    beta_weighted = calculate_beta_weighted_greeks(all_positions, betas=betas)
    concentration = analyze_concentration(beta_weighted['positions'])
    stress_tests = calculate_stress_scenarios(beta_weighted['delta'], beta_weighted['vega'])
    
    raw_totals = {
        'delta': sum(pos['delta'] for pos in all_positions),
        'gamma': sum(pos.get('gamma', 0) for pos in all_positions),
        'vega': sum(pos.get('vega', 0) for pos in all_positions),
        'theta': sum(pos.get('theta', 0) for pos in all_positions)
    }

    # Composition: % portfolio capital in options, equities, cash
    # Use actual premium dollars for options (price * multiplier * qty)
    # and market value for equities/futures (|shares| * spot).
    options_notional = 0.0
    equities_notional = 0.0
    for pos in option_positions:
        try:
            price_per_share = float(pos.get('option_price', 0.0))
            mult = float(pos.get('multiplier', 100.0))
            qty = float(pos.get('qty', 0.0))
            options_notional += abs(price_per_share * mult * qty)
        except Exception:
            pass
    for pos in underlying_positions:
        try:
            shares = float(pos.get('delta_shares', 0.0))
            spot = float(pos.get('spot', 0.0))
            equities_notional += abs(shares * spot)
        except Exception:
            pass
    total_invested = options_notional + equities_notional
    composition = {
        'options_notional': options_notional,
        'equities_notional': equities_notional,
        'total_invested': total_invested,
        'pct_options': (options_notional / total_invested * 100.0) if total_invested else 0.0,
        'pct_equities': (equities_notional / total_invested * 100.0) if total_invested else 0.0,
        # pct_cash can be filled in by caller if cash is known; leave as None for now
        'pct_cash': None,
    }

    # Long / Short evaluation by delta-dollar sign
    def _bucketize(dd_list: list[float]) -> dict:
        long_dd = sum(d for d in dd_list if d > 0)
        short_dd = sum(-d for d in dd_list if d < 0)  # positive number
        net_dd = long_dd - short_dd
        gross_dd = long_dd + short_dd
        num_long = sum(1 for d in dd_list if d > 0)
        num_short = sum(1 for d in dd_list if d < 0)
        return {
            'long_dd': long_dd,
            'short_dd': short_dd,
            'net_dd': net_dd,
            'gross_dd': gross_dd,
            'num_long': num_long,
            'num_short': num_short,
            'pct_long': (long_dd / gross_dd * 100.0) if gross_dd else 0.0,
            'pct_short': (short_dd / gross_dd * 100.0) if gross_dd else 0.0,
        }

    option_dds: list[float] = []
    for pos in option_positions:
        try:
            option_dds.append(float(pos.get('delta', 0.0)) * float(pos.get('spot', 0.0)))
        except Exception:
            pass
    equity_dds: list[float] = []
    for pos in underlying_positions:
        try:
            equity_dds.append(float(pos.get('delta_shares', 0.0)) * float(pos.get('spot', 0.0)))
        except Exception:
            pass

    ls_options = _bucketize(option_dds)
    ls_equities = _bucketize(equity_dds)
    ls_portfolio = _bucketize(option_dds + equity_dds)
    
    # Risk flags
    risk_flags = []
    if concentration['by_symbol'] and max(concentration['by_symbol'].values()) > 30:
        top_symbol = max(concentration['by_symbol'], key=concentration['by_symbol'].get)
        risk_flags.append(f"HIGH CONCENTRATION: {top_symbol} = {concentration['by_symbol'][top_symbol]:.1f}% of portfolio")
    if abs(beta_weighted['delta']) > 2000:
        risk_flags.append(f"HIGH BETA-WEIGHTED DELTA: {beta_weighted['delta']:.0f} SPY-equivalent shares")
    if beta_weighted['theta'] < -1000:
        risk_flags.append(f"HIGH THETA BURN: ${abs(beta_weighted['theta']):.0f}/day decay")
    
    return {
        'timestamp': datetime.now().isoformat(),
        'raw_totals': raw_totals,
        'beta_weighted_totals': {k: beta_weighted[k] for k in ['delta', 'gamma', 'vega', 'theta']},
        'amplification_factor': (abs(beta_weighted['delta']) / abs(raw_totals['delta'])) if raw_totals['delta'] else 1.0,
        'concentration': concentration,
        'stress_scenarios': stress_tests,
        'composition': composition,
        'long_short': {
            'options': ls_options,
            'equities': ls_equities,
            'portfolio': ls_portfolio,
        },
        'risk_flags': risk_flags,
        'position_count': {'total': len(all_positions), 'options': len(option_positions), 'stocks': len(underlying_positions)}
    }


def main() -> int:
    args = parse_args()

    ib = IB()
    print(f"Connecting to IBKR at {args.host}:{args.port} (clientId={args.client_id})...")
    try:
        ib.connect(args.host, args.port, clientId=args.client_id)
    except Exception as e:
        print("Failed to connect to IBKR.")
        print("- Ensure TWS/IB Gateway is running and API is enabled")
        print("- Verify host/port")
        print(f"Error: {e}")
        return 2

    # Set desired market data type
    ib.reqMarketDataType(args.md_type)

    # Determine account filter
    accounts: list[str]
    if args.account:
        accounts = [a.strip() for a in args.account.split(",") if a.strip()]
    elif args.accounts:
        accounts = [a.strip() for a in args.accounts.split(",") if a.strip()]
    else:
        accounts = []  # means include all

    # Collect positions (initial snapshot) and keep updated on events
    positions: list[Position] = ib.positions()
    if accounts:
        positions = [p for p in positions if p.account in accounts]

    print(f"Loaded {len(positions)} positions (accounts filter: {accounts or 'ALL'})")

    # Request market data for all positions we'll need
    conId_to_ticker: dict[int, any] = {}
    conId_first_seen: dict[int, float] = {}
    conId_has_greeks: set[int] = set()
    conId_to_symbol: dict[int, str] = {}  # For debugging

    # Subscribe to underlying stocks first (needed for options Greeks modeling)
    underlying_symbols = set()
    for p in positions:
        c = p.contract
        if getattr(c, "secType", "") in ("OPT", "FOP"):
            underlying_symbols.add(getattr(c, "symbol", ""))
        elif getattr(c, "secType", "") in ("STK", "FUT"):
            underlying_symbols.add(getattr(c, "symbol", ""))
    
    if args.debug:
        print(f"Found {len(underlying_symbols)} unique underlying symbols: {sorted(underlying_symbols)}")

    # Subscribe to underlyings first
    for symbol in underlying_symbols:
        if symbol:
            try:
                stock = Stock(symbol, "SMART", "USD")
                qualified = ib.qualifyContracts(stock)
                if qualified and qualified[0]:
                    stock = qualified[0]
                    t = ib.reqMktData(stock, "", False, False)
                    conId_to_ticker[stock.conId] = t
                    conId_first_seen[stock.conId] = time.time()
                    conId_to_symbol[stock.conId] = symbol
                    if args.debug:
                        print(f"Subscribed to underlying: {symbol} (conId={stock.conId})")
            except Exception as e:
                if args.debug:
                    print(f"Failed to subscribe to underlying {symbol}: {e}")

    # Optionally fetch live Betas for underlyings using generic tick 258 (snapshot)
    if args.fetch_beta and underlying_symbols:
        for symbol in sorted(underlying_symbols):
            try:
                # Skip index symbols that are not equities/ETFs (basic heuristic)
                if symbol.upper() in {"SPX", "NDX", "VIX"}:
                    continue
                st = Stock(symbol, "SMART", "USD")
                q = ib.qualifyContracts(st)
                st = q[0] if q and q[0] else st
                tk = ib.reqMktData(st, "258", True, False)
                # Wait up to ~6s for ratios to arrive
                ratios = None
                for _ in range(20):
                    ratios = getattr(tk, 'fundamentalRatios', None)
                    if ratios:
                        break
                    ib.sleep(0.3)
                beta_val = None
                if ratios and hasattr(ratios, '__dict__'):
                    d = ratios.__dict__
                    beta_val = d.get('BETA') or d.get('beta') or d.get('Beta')
                # Fallback: XML fundamental report
                if beta_val is None:
                    try:
                        xml = util.run(ib.reqFundamentalDataAsync(st, 'ReportRatios'))
                        if xml:
                            m = re.search(r"beta[^\d-]*([-+]?[0-9]*\.?[0-9]+)", xml, re.IGNORECASE)
                            if m:
                                beta_val = float(m.group(1))
                    except Exception:
                        pass
                if beta_val is not None:
                    try:
                        RUNTIME_BETAS[symbol] = float(beta_val)
                        if args.debug:
                            print(f"Fetched Beta {symbol} = {RUNTIME_BETAS[symbol]:.3f}")
                    except Exception:
                        pass
                elif args.debug:
                    print(f"No Beta returned for {symbol}; using default {DEFAULT_BETAS.get(symbol, 1.0)}")
            except Exception as e:
                if args.debug:
                    print(f"Beta fetch failed for {symbol}: {e}")

    # Now subscribe to options using their exact conIds
    from ib_async.contract import Contract as IbContract
    option_count = 0
    for p in positions:
        c = p.contract
        if getattr(c, "secType", "") in ("OPT", "FOP") and getattr(c, "conId", 0):
            option_count += 1
            conId_to_symbol[c.conId] = getattr(c, 'localSymbol', getattr(c, 'symbol', str(c.conId)))
            
            # Always qualify the option contract before requesting market data
            try:
                fresh_contract = IbContract(conId=c.conId)
                qualified = ib.qualifyContracts(fresh_contract)
                qc = qualified[0] if qualified and qualified[0] else fresh_contract
                t = ib.reqMktData(qc, "100,101,104,106", False, False)
            except Exception as e:
                if args.debug:
                    sym = conId_to_symbol[c.conId]
                    print(f"Option subscription failed: {sym} (conId={c.conId}): {e}")
                continue
            conId_to_ticker[c.conId] = t
            conId_first_seen[c.conId] = time.time()
            if args.debug:
                sym = conId_to_symbol[c.conId]
                print(f"Subscribed to option: {sym} (conId={c.conId})")
        elif getattr(c, "secType", "") in ("STK", "FUT") and c.conId not in conId_to_ticker:
            # Subscribe to any stocks not already covered by underlyings
            conId_to_symbol[c.conId] = getattr(c, 'symbol', str(c.conId))
            try:
                t = ib.reqMktData(c, "", False, False)
                conId_to_ticker[c.conId] = t
                conId_first_seen[c.conId] = time.time()
                if args.debug:
                    print(f"Subscribed to stock/future: {conId_to_symbol[c.conId]} (conId={c.conId})")
            except Exception as e:
                if args.debug:
                    print(f"Stock/future subscription failed: {conId_to_symbol[c.conId]} (conId={c.conId}): {e}")
    
    if args.debug:
        print(f"Total subscriptions: {len(conId_to_ticker)} ({option_count} options, {len(underlying_symbols)} underlyings)")

    # Keep positions fresh when they change in TWS/IB
    def on_position_update(pos: Position) -> None:
        if accounts and pos.account not in accounts:
            return
        # replace or remove in local list
        nonlocal positions
        positions = [x for x in positions if x.contract.conId != pos.contract.conId or x.account != pos.account]
        if pos.position != 0:
            positions.append(pos)
            # Re-subscribe for new positions (simplified - no double subscription issue)
            c = pos.contract
            if c.conId not in conId_to_ticker:
                conId_to_symbol[c.conId] = getattr(c, 'localSymbol', getattr(c, 'symbol', str(c.conId)))
                try:
                    if getattr(c, "secType", "") in ("OPT", "FOP"):
                        from ib_async.contract import Contract as IbContract
                        fresh_contract = IbContract(conId=c.conId)
                        qualified = ib.qualifyContracts(fresh_contract)
                        qc = qualified[0] if qualified and qualified[0] else fresh_contract
                        t = ib.reqMktData(qc, "100,101,104,106", False, False)
                    else:
                        t = ib.reqMktData(c, "", False, False)
                    conId_to_ticker[c.conId] = t
                    conId_first_seen[c.conId] = time.time()
                    if args.debug:
                        print(f"New position subscription: {conId_to_symbol[c.conId]} (conId={c.conId})")
                except Exception as e:
                    if args.debug:
                        print(f"New position subscription failed: {conId_to_symbol[c.conId]}: {e}")

    ib.positionEvent += on_position_update

    # Give more time for options Greeks to populate (especially important for options)
    if args.debug:
        print(f"Waiting {max(5, args.warmup) if not args.once else 5}s for market data to populate...")
    ib.sleep(max(5, args.warmup) if not args.once else 5)

    # Prepare output file
    outpath = os.path.abspath(args.outfile)
    # Ensure directory exists if a nested path was provided
    outdir = os.path.dirname(outpath)
    if outdir and not os.path.isdir(outdir):
        os.makedirs(outdir, exist_ok=True)
    fp = open(outpath, "a", buffering=1)
    print(f"Streaming Greek snapshots every {args.interval}s → {outpath}")

    # Optional HTTP static server for easy dashboard access
    httpd: ThreadingHTTPServer | None = None
    server_thread: threading.Thread | None = None

    if args.serve:
        serve_dir = outdir if outdir else PROJECT_ROOT

        class CORSHandler(SimpleHTTPRequestHandler):
            def __init__(self, *hargs, **hkwargs):
                super().__init__(*hargs, directory=serve_dir, **hkwargs)

            def end_headers(self):
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Cache-Control', 'no-store')
                super().end_headers()

        # Try binding; if port in use, pick a random free port
        port = args.http_port
        try:
            httpd = ThreadingHTTPServer(('127.0.0.1', port), CORSHandler)
        except OSError:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', 0))
                port = s.getsockname()[1]
            httpd = ThreadingHTTPServer(('127.0.0.1', port), CORSHandler)

        def run_server():
            try:
                print(f"Serving {serve_dir} at http://127.0.0.1:{port}/ (CORS enabled)")
                httpd.serve_forever()
            except Exception as e:
                print(f"HTTP server stopped: {e}")

        server_thread = threading.Thread(target=run_server, name='greeks-http', daemon=True)
        server_thread.start()

    def snapshot_once() -> None:
        timestamp = iso_now()
        # Aggregation by underlying symbol
        agg: DefaultDict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        # Also aggregate a portfolio total across all underlyings
        portfolio: Dict[str, float] = defaultdict(float)
        # Track individual option positions for detailed display
        option_positions = []
        # Track non-option (stocks/futures) positions for separate display
        stock_positions = []
        # Track cash balances per account/currency for display
        cash_positions = []

        # Helper to add into both underlying bucket and portfolio
        def add(und: str, key: str, value: float) -> None:
            agg[und][key] += float(value)
            portfolio[key] += float(value)

        # Iterate positions and sum
        current_positions = list(positions)
        for p in current_positions:
            c = p.contract
            qty = float(p.position)
            sec = getattr(c, "secType", "")
            und_symbol = getattr(c, "symbol", getattr(c, "localSymbol", "?"))
            mult = multiplier_of(c)

            if sec in ("OPT", "FOP"):
                # Skip closed positions (qty == 0)
                if qty == 0:
                    continue
                t = conId_to_ticker.get(c.conId)
                if t is None:
                    continue
                # Black-Scholes if we can; else fallback to IB greeks
                g = None
                # Try to locate an underlying ticker for spot
                und_ticker = None
                for tid, ticker in conId_to_ticker.items():
                    if conId_to_symbol.get(tid, '') == und_symbol:
                        und_ticker = ticker
                        break

                # Pull IV from multiple sources, sanitize NaN/None
                iv_sources = [
                    getattr(t, 'impliedVolatility', None),
                    getattr(getattr(t, 'modelGreeks', None), 'impliedVol', None),
                    getattr(getattr(t, 'lastGreeks', None), 'impliedVol', None),
                    getattr(getattr(t, 'bidGreeks', None), 'impliedVol', None),
                    getattr(getattr(t, 'askGreeks', None), 'impliedVol', None),
                ]
                iv = next((v for v in iv_sources if is_positive_finite(v)), None)
                # Parse expiry and compute time to expiry and days remaining (calendar, ceil)
                exp_str = getattr(c, 'lastTradeDateOrContractMonth', '')
                exp_date = None
                try:
                    if exp_str:
                        if len(exp_str) == 8:  # YYYYMMDD
                            exp_date = datetime.strptime(exp_str, '%Y%m%d')
                        else:  # YYMMDD
                            exp_date = datetime.strptime('20' + exp_str, '%Y%m%d')
                except Exception:
                    exp_date = None

                # Compute time remaining until US market close (4pm ET) on expiry date, in UTC
                now_utc = datetime.now(timezone.utc)
                exp_close_utc = expiry_in_utc_for_us_equity_options(exp_date) if exp_date else None
                time_to_exp = ((exp_close_utc - now_utc).total_seconds() / (365.25 * 24 * 3600)) if exp_close_utc else 0.0
                # Display-oriented remaining full days (floor), not ceiling.
                # On expiration day before close, show 0 instead of 1.
                if exp_close_utc:
                    rem_days = math.floor((exp_close_utc - now_utc).total_seconds() / 86400.0)
                    days_to_exp = max(0, int(rem_days))
                else:
                    days_to_exp = None

                # If already past expiration close, ignore this option completely
                # Note: Keep just-expired options for 2 hours to let TWS update quantities
                if exp_close_utc and (now_utc - exp_close_utc).total_seconds() > 2 * 3600:
                    continue

                und_price = None
                if und_ticker is not None:
                    # Gather multiple spot sources and pick a sane one
                    spot_sources = [
                        getattr(und_ticker, 'last', None),
                        getattr(und_ticker, 'close', None),
                        getattr(und_ticker, 'marketPrice', None),
                        getattr(und_ticker, 'bid', None) and getattr(und_ticker, 'ask', None) and (getattr(und_ticker, 'bid') + getattr(und_ticker, 'ask')) / 2.0 or None,
                    ]
                    und_price = next((v for v in spot_sources if is_positive_finite(v)), None)

                option_type = getattr(c, 'right', 'C')
                strike = float(getattr(c, 'strike', 0) or 0)
                r = 0.05  # Assume 5% risk-free rate

                if is_positive_finite(iv) and und_price and und_price > 0 and time_to_exp > 0:
                    try:
                        g = calculate_bs_greeks(und_price, strike, time_to_exp, r, iv, option_type)
                        if g and args.debug:
                            sym = conId_to_symbol.get(c.conId, f"conId_{c.conId}")
                            print(f"BS Greeks: {sym} delta={g.delta:.4f} gamma={g.gamma:.4f} vega={g.vega:.4f} theta={g.theta:.4f}")
                    except Exception as e:
                        if args.debug:
                            print(f"BS calculation failed for {conId_to_symbol.get(c.conId, c.conId)}: {e}")

                # Fallback to IB Greeks only if Black-Scholes failed
                if g is None:
                    g = find_best_greeks(t)
                
                if g is None:
                    # Attempt to back out IV from price if possible
                    # Use mid price from option and a valid spot to estimate IV, then compute Greeks
                    opt_mid = None
                    bid = getattr(t, 'bid', None)
                    ask = getattr(t, 'ask', None)
                    if is_positive_finite(bid) and is_positive_finite(ask):
                        opt_mid = 0.5 * (bid + ask)
                    if (not is_positive_finite(opt_mid)):
                        last_px = getattr(t, 'last', None)
                        if is_positive_finite(last_px):
                            opt_mid = last_px
                    if (not is_positive_finite(opt_mid)):
                        close_px = getattr(t, 'close', None)
                        if is_positive_finite(close_px):
                            opt_mid = close_px

                    est_iv = None
                    if is_positive_finite(opt_mid) and is_positive_finite(und_price) and strike > 0 and time_to_exp > 0:
                        est_iv = implied_vol_from_price(und_price, strike, time_to_exp, r, opt_mid, option_type)
                        if is_positive_finite(est_iv):
                            try:
                                g = calculate_bs_greeks(und_price, strike, time_to_exp, r, est_iv, option_type)
                                if args.debug and g:
                                    sym = conId_to_symbol.get(c.conId, f"conId_{c.conId}")
                                    print(f"Backsolved IV for {sym}: iv={est_iv:.4f}")
                            except Exception:
                                g = None

                if g is None:
                    # Debug: show which options are missing Greeks and IV
                    if args.debug:
                        sym = conId_to_symbol.get(c.conId, f"conId_{c.conId}")
                        print(f"No Greeks or IV: {sym} IV={iv}")
                    continue
                else:
                    # Debug: confirm we found Greeks (only first time)
                    if args.debug and c.conId not in conId_has_greeks:
                        sym = conId_to_symbol.get(c.conId, f"conId_{c.conId}")
                        print(f"Greeks found: {sym} delta={g.delta:.4f} gamma={g.gamma:.4f} vega={g.vega:.4f} theta={g.theta:.4f}")
                    conId_has_greeks.add(c.conId)
                und_price = (g.undPrice if is_positive_finite(getattr(g, 'undPrice', None)) else (und_price or 0.0))
                delta = (g.delta or 0.0) * qty * mult
                gamma = (g.gamma or 0.0) * qty * mult
                vega = (g.vega or 0.0) * qty * mult
                theta = (g.theta or 0.0) * qty * mult

                # Derived exposures
                gamma_delta_1pct = gamma * (und_price * 0.01)
                dollar_gamma_1pct = 0.5 * (g.gamma or 0.0) * (und_price * 0.01) ** 2 * qty * mult
                dollar_delta = delta * und_price
                dollar_vega_1volpt = vega  # IB vega is already per 1 vol point
                dollar_theta_day = theta

                if args.debug:
                    try:
                        sym = conId_to_symbol.get(c.conId, f"conId_{c.conId}")
                        print(
                            f"Theta detail: {sym} S={und_price:.2f} K={strike:.2f} T_days={(time_to_exp*365.25):.2f} "
                            f"iv={(iv_final if 'iv_final' in locals() and iv_final is not None else iv)} "
                            f"per_day={float(getattr(g,'theta',0.0)):.6f} qty={qty} mult={mult} pos_theta={dollar_theta_day:.2f}"
                        )
                    except Exception:
                        pass

                add(und_symbol, "delta_shares", delta)
                add(und_symbol, "delta_dollars", dollar_delta)
                add(und_symbol, "gamma_1pct_delta", gamma_delta_1pct)
                add(und_symbol, "gamma_dollar_1pct", dollar_gamma_1pct)
                add(und_symbol, "vega_dollar_1volpt", dollar_vega_1volpt)
                add(und_symbol, "theta_dollar_day", dollar_theta_day)
                # Track a simple spot estimate; last seen wins per underlying
                agg[und_symbol]["spot"] = und_price
                
                # Track individual option position details
                # Build expiry display and additional metrics
                if exp_date:
                    exp_display = exp_date.strftime('%m/%d/%y')
                else:
                    exp_display = exp_str or ''

                # Final IV to use for probability calc
                iv_final = None
                try:
                    iv_final = (iv if is_positive_finite(iv) else getattr(g, 'impliedVol', None))
                except Exception:
                    iv_final = iv if (iv and iv > 0) else None
                # As a last resort, use backsolved IV if greeks were computed using it
                if not is_positive_finite(iv_final):
                    # Try to recompute iv from option_price if we have one
                    opt_mid = None
                    bid = getattr(t, 'bid', None)
                    ask = getattr(t, 'ask', None)
                    if is_positive_finite(bid) and is_positive_finite(ask):
                        opt_mid = 0.5 * (bid + ask)
                    if (not is_positive_finite(opt_mid)):
                        last_px = getattr(t, 'last', None)
                        if is_positive_finite(last_px):
                            opt_mid = last_px
                    if (not is_positive_finite(opt_mid)):
                        close_px = getattr(t, 'close', None)
                        if is_positive_finite(close_px):
                            opt_mid = close_px
                    if is_positive_finite(opt_mid) and is_positive_finite(und_price) and strike > 0 and time_to_exp > 0:
                        est_iv = implied_vol_from_price(und_price, strike, time_to_exp, r, opt_mid, option_type)
                        if is_positive_finite(est_iv):
                            iv_final = est_iv

                # Probability of expiring ITM under risk-neutral measure
                prob_itm = None
                if is_positive_finite(iv_final) and time_to_exp and time_to_exp > 0 and und_price and und_price > 0 and strike > 0:
                    try:
                        sqrtT = math.sqrt(time_to_exp)
                        d1 = (math.log(und_price / strike) + (r + 0.5 * iv_final * iv_final) * time_to_exp) / (iv_final * sqrtT)
                        d2 = d1 - iv_final * sqrtT
                        if option_type.upper() == 'C':
                            prob_itm = float(norm.cdf(d2))
                        else:
                            prob_itm = float(norm.cdf(-d2))
                    except Exception:
                        prob_itm = None

                # Percent move to become ITM (signed; 0 if already ITM)
                move_to_itm_pct = None
                if und_price and und_price > 0 and strike > 0:
                    raw = (strike - und_price) / und_price * 100.0
                    if option_type.upper() == 'C':
                        move_to_itm_pct = max(0.0, raw)
                    else:
                        # For puts, becoming ITM requires a decrease; show negative move (or 0 if already ITM)
                        move_to_itm_pct = min(0.0, raw)

                # Percent move to double option value (approx via delta/gamma quadratic)
                pct_move_to_double = None
                option_price_per_share = None
                try:
                    # Determine current option price per share using best available source
                    price_per_share = getattr(g, 'price', None)
                    if not price_per_share or price_per_share <= 0:
                        # Try last price
                        opt_last = getattr(t, 'last', None)
                        if opt_last and opt_last > 0:
                            price_per_share = opt_last
                    if (not price_per_share or price_per_share <= 0):
                        # Try bid/ask mid
                        bid = getattr(t, 'bid', None)
                        ask = getattr(t, 'ask', None)
                        if bid and ask and bid > 0 and ask > 0:
                            price_per_share = (bid + ask) / 2.0
                    if (not price_per_share or price_per_share <= 0):
                        # Try prior close
                        close_px = getattr(t, 'close', None)
                        if close_px and close_px > 0:
                            price_per_share = close_px
                    if (not price_per_share or price_per_share <= 0) and is_positive_finite(iv_final) and und_price and und_price > 0 and time_to_exp and time_to_exp > 0:
                        # As a last resort, compute theoretical price using BS with available IV
                        bs_price_obj = calculate_bs_greeks(und_price, strike, time_to_exp, r, iv_final, option_type)
                        if bs_price_obj and getattr(bs_price_obj, 'price', None):
                            price_per_share = bs_price_obj.price

                    option_price_per_share = price_per_share if (price_per_share and price_per_share > 0) else None

                    if option_price_per_share and und_price and und_price > 0:
                        price_contract = price_per_share * mult
                        delta_contract = (getattr(g, 'delta', 0.0) or 0.0) * mult
                        gamma_contract = (getattr(g, 'gamma', 0.0) or 0.0) * mult
                        if gamma_contract and gamma_contract > 0 and delta_contract is not None and price_contract > 0:
                            disc = abs(delta_contract) * abs(delta_contract) + 2.0 * gamma_contract * price_contract
                            root_mag = (-abs(delta_contract) + math.sqrt(disc)) / (gamma_contract if gamma_contract != 0 else 1e-9)
                            dS = root_mag if option_type.upper() == 'C' else -root_mag
                            pct_move_to_double = (dS / und_price) * 100.0
                        elif delta_contract and delta_contract != 0:
                            # Linear fallback
                            dS = (price_contract / abs(delta_contract)) * (1.0 if option_type.upper() == 'C' else -1.0)
                            pct_move_to_double = (dS / und_price) * 100.0
                except Exception:
                    pct_move_to_double = None
                
                option_positions.append({
                    "symbol": und_symbol,
                    "strike": getattr(c, 'strike', 0),
                    "expiry": exp_display,
                    "right": getattr(c, 'right', ''),
                    "qty": qty,
                    "multiplier": mult,
                    "delta": delta,
                    "delta_dollars": dollar_delta,
                    "gamma": gamma,  # Already calculated as total for position
                    "vega": vega,    # Already calculated as total for position  
                    "theta": theta,  # Already calculated as total for position
                    "spot": und_price,
                    # Additional analytics for dashboard
                    "days_to_exp": days_to_exp,
                    "iv": round(float(iv_final), 6) if is_positive_finite(iv_final) else None,
                    "prob_itm": round(float(prob_itm), 6) if (prob_itm is not None) else None,
                    "pct_move_to_itm": round(float(move_to_itm_pct), 6) if (move_to_itm_pct is not None) else None,
                    "pct_move_to_double": round(float(pct_move_to_double), 6) if (pct_move_to_double is not None) else None,
                    "option_price": round(float(option_price_per_share), 6) if (option_price_per_share is not None) else None,
                })

            elif sec in ("STK", "FUT"):
                # Stock/future contribute only to delta; dollars if we have price
                t = conId_to_ticker.get(c.conId)
                spot = getattr(t, "last", float("nan")) if t else float("nan")
                delta_shares = qty * mult
                add(und_symbol, "delta_shares", delta_shares)
                delta_dollars = None
                if spot == spot:  # not NaN
                    delta_dollars = delta_shares * spot
                    add(und_symbol, "delta_dollars", delta_dollars)
                    agg[und_symbol]["spot"] = spot

                stock_positions.append({
                    "symbol": und_symbol,
                    "type": "future" if sec == "FUT" else "stock",
                    "qty": qty,
                    "multiplier": mult,
                    "delta_shares": delta_shares,
                    "delta_dollars": delta_dollars,
                    "spot": spot if spot == spot else None,
                    "conId": getattr(c, 'conId', None),
                    "account": getattr(p, 'account', None),
                })

        # Emit underlying records
        for und, metrics in agg.items():
            rec = {
                "timestamp": timestamp,
                "scope": "underlying",
                "account": "ALL" if not accounts else ",".join(sorted(set(accounts))),
                "symbol": und,
            }
            rec.update({k: round(v, 6) for k, v in metrics.items()})
            fp.write(json.dumps(rec) + "\n")
            if args.do_print:
                spot = rec.get("spot")
                print(f"{timestamp}  {und:>10}  spot={spot if spot is not None else 'nan'}  "
                      f"Δ_sh={rec.get('delta_shares', 0):.2f}  $Δ={rec.get('delta_dollars', 0):.2f}  "
                      f"ΓΔ@1%={rec.get('gamma_1pct_delta', 0):.2f}  $Γ@1%={rec.get('gamma_dollar_1pct', 0):.2f}  "
                      f"$V@1vol={rec.get('vega_dollar_1volpt', 0):.2f}  $Θ/day={rec.get('theta_dollar_day', 0):.2f}")

        # Emit individual option positions
        for opt in option_positions:
            opt_rec = {
                "timestamp": timestamp,
                "scope": "option",
                "account": "ALL" if not accounts else ",".join(sorted(set(accounts))),
                "symbol": opt["symbol"],
                "strike": opt["strike"],
                "expiry": opt["expiry"],
                "right": opt["right"],
                "qty": opt["qty"],
                    "multiplier": opt.get("multiplier", 100),
                "delta": round(opt["delta"], 6),
                    "delta_dollars": round(opt["delta_dollars"], 6),
                "gamma": round(opt["gamma"], 6),
                "vega": round(opt["vega"], 6),
                "theta": round(opt["theta"], 6),
                "spot": round(opt["spot"], 2) if opt["spot"] else None,
                # New fields for dashboard calculations
                "days_to_exp": int(opt["days_to_exp"]) if opt.get("days_to_exp") is not None else None,
                "iv": opt.get("iv"),
                "prob_itm": opt.get("prob_itm"),
                "pct_move_to_itm": opt.get("pct_move_to_itm"),
                "pct_move_to_double": opt.get("pct_move_to_double"),
                "option_price": opt.get("option_price"),
            }
            fp.write(json.dumps(opt_rec) + "\n")

        # Emit individual non-option (stock/future) positions
        for stk in stock_positions:
            stk_rec = {
                "timestamp": timestamp,
                "scope": "stock",
                "account": "ALL" if not accounts else ",".join(sorted(set(accounts))),
                "symbol": stk.get("symbol"),
                "type": stk.get("type"),
                "qty": stk.get("qty"),
                "multiplier": stk.get("multiplier"),
                "delta_shares": round(float(stk.get("delta_shares", 0.0)), 6),
                "delta_dollars": round(float(stk.get("delta_dollars")) , 6) if stk.get("delta_dollars") is not None else None,
                "spot": round(float(stk.get("spot")), 2) if stk.get("spot") is not None else None,
                "conId": stk.get("conId"),
            }
            fp.write(json.dumps(stk_rec) + "\n")

        # Collect and emit cash balances as 'stock' scope with type 'cash'
        try:
            acct_ids = accounts if accounts else ib.managedAccounts()
            # Parse currency whitelist once
            whitelist = set(x.strip().upper() for x in (args.cash_currencies.split(',') if args.cash_currencies else []) if x.strip())
            for acct in acct_ids:
                try:
                    summary = ib.accountSummary(acct)
                    # Use only exact CashBalance per currency to avoid double-counting from TotalCashValue.
                    cash_balance_by_ccy: dict[str, float] = {}
                    for item in summary:
                        tag = getattr(item, 'tag', '')
                        cur = (getattr(item, 'currency', '') or 'USD').upper()
                        try:
                            val = float(getattr(item, 'value', 'nan'))
                        except Exception:
                            val = float('nan')
                        if not (val == val):
                            continue
                        if tag == 'CashBalance':
                            cash_balance_by_ccy[cur] = cash_balance_by_ccy.get(cur, 0.0) + val
                    # Emit only CashBalance; if none present, skip (avoid TotalCashValue ambiguity)
                    for ccy, amount in cash_balance_by_ccy.items():
                        if whitelist and ccy not in whitelist:
                            continue
                        if abs(amount) < 0.01:
                            continue
                        cash_positions.append({
                            'account': acct,
                            'currency': ccy,
                            'amount': amount,
                        })
                except Exception:
                    continue
        except Exception:
            pass

        for cash in cash_positions:
            cash_rec = {
                "timestamp": timestamp,
                "scope": "stock",
                "account": cash.get("account"),
                "symbol": cash.get("currency", "CASH"),
                "type": "cash",
                "qty": None,
                "multiplier": 1,
                "delta_shares": None,
                "delta_dollars": round(float(cash.get("amount", 0.0)), 2),
                "spot": None,
                "conId": None,
            }
            fp.write(json.dumps(cash_rec) + "\n")

        # Emit portfolio total
        if portfolio:
            recp = {
                "timestamp": timestamp,
                "scope": "portfolio",
                "account": "ALL" if not accounts else ",".join(sorted(set(accounts))),
            }
            recp.update({k: round(v, 6) for k, v in portfolio.items()})
            fp.write(json.dumps(recp) + "\n")
            
        # Generate and emit risk assessment (even if no options)
        try:
                # Get latest underlying data for risk calc (stocks/futures ONLY)
                underlying_data = []
                for stk in stock_positions:
                    underlying_data.append({
                        'symbol': stk.get('symbol'),
                        'delta_shares': stk.get('delta_shares', 0),
                        'spot': stk.get('spot'),
                    })
                
                risk_summary = generate_risk_summary(underlying_data, option_positions, betas=(RUNTIME_BETAS if args.fetch_beta else None))
                # If we captured cash earlier in this snapshot, enrich composition
                try:
                    cash_total = 0.0
                    for cp in cash_positions:
                        try:
                            cash_total += float(cp.get('amount', 0.0))
                        except Exception:
                            pass
                    if cash_total and 'composition' in risk_summary:
                        comp = risk_summary['composition']
                        invested = float(comp.get('total_invested') or 0.0)
                        denom = invested + cash_total
                        if denom > 0:
                            comp['pct_cash'] = cash_total / denom * 100.0
                            comp['pct_options'] = (float(comp.get('options_notional', 0.0)) / denom) * 100.0
                            comp['pct_equities'] = (float(comp.get('equities_notional', 0.0)) / denom) * 100.0
                except Exception:
                    pass
                risk_record = {
                    "timestamp": timestamp,
                    "scope": "risk_assessment",
                    "account": "ALL" if not accounts else ",".join(sorted(set(accounts))),
                    **risk_summary
                }
                fp.write(json.dumps(risk_record) + "\n")
                
                if args.debug:
                    print(f"Risk Assessment - Beta-weighted delta: {risk_summary['beta_weighted_totals']['delta']:.0f}, "
                          f"Amplification: {risk_summary['amplification_factor']:.1f}x, "
                          f"Flags: {len(risk_summary['risk_flags'])}")
                    
        except Exception as e:
            if args.debug:
                print(f"Risk calculator error: {e}")
                import traceback
                traceback.print_exc()
                
        if args.do_print:
            print(f"{timestamp}  {'PORTFOLIO':>10}  "
                  f"Δ_sh={recp.get('delta_shares', 0):.2f}  $Δ={recp.get('delta_dollars', 0):.2f}  "
                  f"ΓΔ@1%={recp.get('gamma_1pct_delta', 0):.2f}  $Γ@1%={recp.get('gamma_dollar_1pct', 0):.2f}  "
                  f"$V@1vol={recp.get('vega_dollar_1volpt', 0):.2f}  $Θ/day={recp.get('theta_dollar_day', 0):.2f}")

        fp.flush()

    try:
        print("Press Ctrl+C to stop.")
        if args.once:
            if args.warmup and args.warmup > 0:
                if args.do_print:
                    print(f"Warming up for {args.warmup}s before snapshot…")
                ib.sleep(args.warmup)
            snapshot_once()
        else:
            while True:
                snapshot_once()
                ib.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        try:
            fp.close()
        except Exception:
            pass
        if httpd:
            try:
                httpd.shutdown()
            except Exception:
                pass
        status = ib.disconnect()
        if status:
            print(status)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
