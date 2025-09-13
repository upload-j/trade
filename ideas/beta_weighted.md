
# Beta‑Weighted Portfolio Delta — Pseudocode (IB Connected)

Goal: express your entire options book as **index‑equivalent delta** (e.g., SPY shares).

---

## Inputs
- **Benchmark**: `SPY` (or your chosen index ETF).
- **Positions** from IB (all underlyings, options + stock).
- **Market Prices**: last/close for each underlying and the benchmark.
- **Per‑position Delta** (share‑equivalent). Source priority:
  1) IB option greeks feed (preferred), else
  2) Recompute via BS using market IV.
- **Beta per underlying** relative to benchmark. Source priority:
  1) IB Fundamentals beta (if available)
  2) Rolling regression vs benchmark (e.g., 252 daily bars)

Contract multiplier for equity options: `100` (verify via IB contract details).

---

## High‑Level Formula
For each underlying *i*:
- **Net delta in shares**:  
  `Δ_i_shares = Σ_over_positions( position_delta × position_qty × multiplier × side )`
- **Dollar delta**:  
  `Δ_i_$ = Δ_i_shares × Spot_i`
- **Beta‑adjusted dollar delta**:  
  `Δ_i_$β = Beta_i × Δ_i_$`

Portfolio:
- **Total beta‑adj dollar delta**:  
  `Δ_port_$β = Σ_i Δ_i_$β`
- **Beta‑weighted SPY‑equivalent delta (shares)**:  
  `Δ_port_in_SPY = Δ_port_$β / Spot_SPY`

---

## Pseudocode (ib_insync‑style)

```pseudo
connect_to_ib()

benchmark = "SPY"

# 1) Pull positions
positions = ib.portfolio()  # returns list with contract, position qty, avgCost, etc.

# 2) Build set of unique underlyings
underlyings = group_positions_by_underlying(positions)  # map: symbol -> list of positions

# 3) Fetch live market data (spots) for each underlying + benchmark
spots = get_spot_prices(underlyings + [benchmark])  # dict[symbol] -> price

# 4) Get deltas per position
for pos in positions:
    if pos.contract is option:
        greeks = request_option_greeks(pos.contract)  # IB reqMktData with OptionComputation
        pos_delta = greeks.delta  # share-equivalent per contract
        multiplier = get_multiplier(pos.contract)     # usually 100 for US equity options
    elif pos.contract is stock:
        pos_delta = 1.0
        multiplier = 1
    side = +1 if pos.position > 0 else -1              # long/short
    pos.delta_shares = pos_delta * abs(pos.position) * multiplier * side

# 5) Net delta per underlying (sum over its positions)
net_delta_shares = dict()  # symbol -> float
for sym, pos_list in underlyings.items():
    net_delta_shares[sym] = sum(p.delta_shares for p in pos_list)

# 6) Obtain beta per underlying (relative to benchmark)
beta = dict()

# 6a) Try IB fundamentals beta
for sym in underlyings.keys():
    b = try_ib_fundamentals_beta(sym)   # reqFundamentalData("Ratios") or similar
    if b is not None:
        beta[sym] = b

# 6b) Fallback: compute via regression if fundamentals beta missing
#      Use ~1 year of daily returns (252 bars)
missing = [sym for sym in underlyings if sym not in beta]
if missing:
    prices_hist = get_history([benchmark] + missing, barSize="1 day", lookback="252 D")
    r_bench = pct_change(prices_hist[benchmark])
    for sym in missing:
        r_sym = pct_change(prices_hist[sym])
        # Ordinary Least Squares: r_sym ~ alpha + beta * r_bench
        beta[sym] = OLS_beta(r_sym, r_bench)

# 7) Compute dollar delta and beta‑adjusted dollar delta per underlying
delta_dollars_beta = dict()
for sym in underlyings.keys():
    spot = spots[sym]
    dd = net_delta_shares[sym] * spot
    dd_beta = beta[sym] * dd
    delta_dollars_beta[sym] = (dd, dd_beta)

# 8) Portfolio totals
total_dd_beta = sum(dd_beta for (_, dd_beta) in delta_dollars_beta.values())
spy_spot = spots[benchmark]
portfolio_beta_weighted_delta_in_spy = total_dd_beta / spy_spot

# 9) Output report
report = table(
    columns=["Symbol", "Spot", "Net Δ (shares)", "Δ$","Beta","Δ$β"],
    rows=[ (sym, spots[sym], net_delta_shares[sym], dd, beta[sym], dd_beta)
           for sym,(dd,dd_beta) in delta_dollars_beta.items() ]
)
print(report)
print("Total β‑adj $Δ:", total_dd_beta)
print("β‑weighted Δ in", benchmark, "shares:", portfolio_beta_weighted_delta_in_spy)
```

---

## Notes & Edge Cases
- **Multiplier**: always read from IB `contract.multiplier` (don’t hardcode 100). Index options, futures, and minis differ.
- **American vs European**: Delta source is market data/greeks; pricing model only matters if you recompute deltas locally.
- **Stale data**: Use snapshot vs streaming consistently; align timestamps if mixing sources.
- **Short positions**: `side = -1` makes deltas reduce net exposure.
- **Beta horizon**: daily returns over 252 days is common; adjust window for your style.
- **Benchmark choice**: for single‑name tech heavy books, QQQ may be a better beta anchor than SPY.
- **Verification**: sanity‑check by comparing *raw* portfolio dollar‑delta vs β‑weighted result—β‑weighting should change scale, not flip signs unexpectedly.

---

## Minimal Function Signatures (for your agent)

```pseudo
get_ib_positions() -> List[Position]
get_spot_prices(symbols: List[str]) -> Dict[str,float]
get_option_delta(contract) -> float
get_multiplier(contract) -> int
try_ib_fundamentals_beta(symbol: str) -> Optional[float]
get_history(symbols, barSize, lookback) -> Dict[str,Series]
OLS_beta(r_sym: Series, r_bench: Series) -> float
beta_weighted_delta_report(benchmark: str="SPY") -> DataFrame
```
