# Greeks Aggregator Dashboard

**One-Click Portfolio Greeks Analysis with Real-Time Dashboard**

## Quick Start

**Double-click:** `start_greeks_server.command`

This will:
1. Connect to your IB Gateway/TWS (port 7497)
2. Calculate portfolio Greeks using Black-Scholes
3. Stream data to dashboard every 2 seconds
4. Auto-open browser to live dashboard

## Core Files (4 total)

- `start_greeks_server.command` - One-click launcher 
- `greeks_aggregate.py` - Main aggregator engine with built-in risk analysis
- `greeks_dashboard.html` - Real-time web dashboard
- `greeks_timeseries.jsonl` - Data output (auto-generated)

## Dashboard Features

### Real-Time Greeks
- **Portfolio totals**: Delta, Gamma, Vega, Theta
- **Per-underlying breakdown** (SPY, NVDA, PLTR, etc.)
- **Individual option positions** with strikes/expiries
- **Time series charts** of Greeks over time

### Risk Assessment
- **Beta-weighted exposure** vs raw Greeks
- **Concentration analysis** by symbol/sector
- **Stress test scenarios** (market corrections, vol changes)
- **Risk flags** for high concentration/theta burn

### Options Details
| Symbol | Strike | Expiry   | Type | Qty | Delta | Gamma | Vega | Theta |
|--------|--------|----------|------|-----|-------|-------|------|-------|
| NVDA   | 180    | 08/15/25 | C    | 5   | 337   | 16.2  | 27.4 | -205  |
| PLTR   | 150    | 02/20/26 | C    | 5   | 386   | 9.7   | 100  | -37   |

## Requirements

- **IB Gateway/TWS** running on port 7497 with API enabled
- **Python 3.8+** with `ib_async`, `scipy`, `numpy`
- **Web browser** for dashboard

## Installation

```bash
pip install ib_async scipy numpy
```

## Manual Commands

```bash
# Terminal mode (no dashboard)
python3 greeks_aggregate.py --debug --print

# One snapshot only  
python3 greeks_aggregate.py --once --warmup 10

# Risk analysis of existing data (built into main aggregator)
```

## How It Works

1. **Connects** to IB Gateway and fetches your positions
2. **Subscribes** to market data for all underlying stocks and options
3. **Calculates Greeks** using Black-Scholes with real-time IV from IB
4. **Aggregates** by underlying symbol and portfolio totals
5. **Streams** to JSON file every 2 seconds
6. **Displays** in real-time web dashboard with auto-refresh

## Key Features

- ✅ **Works when markets closed** (uses Black-Scholes fallback)
- ✅ **Real-time updates** every 2 seconds
- ✅ **All option types** (calls, puts, multiple expirations)
- ✅ **Risk analysis** with beta-weighting and stress tests
- ✅ **Mobile-friendly** dashboard
- ✅ **One-click startup** via shell script

Perfect for monitoring complex options portfolios with multiple underlyings and tracking time decay, volatility exposure, and concentration risk in real-time.

## Next Steps & Future Development

### Current Status ✅
The Greeks Aggregator provides a solid foundation for portfolio risk assessment with:
- **Real-time Greeks calculation** using Black-Scholes when IB doesn't provide Greeks
- **Beta-weighted risk analysis** for market correlation assessment
- **Individual position tracking** with strikes/expiries
- **Basic stress testing** for market scenarios

### Known Limitations ⚠️
The current risk calculations may not be fully accurate and need validation:
- **Beta coefficients** are estimates, not dynamically calculated from actual correlations
- **Stress test scenarios** use simplified assumptions about market movements
- **Portfolio correlation effects** may not reflect real trading behavior
- **Volatility surface assumptions** in Black-Scholes may differ from IB's models

### Planned Enhancements 🚀

#### Phase 1: Core Metrics Optimization Framework
**Objective**: Create a clear cost/return optimization system with 3 key metrics:

1. **DELTA = Return Potential** 📈
   - Beta-weighted delta exposure as proxy for directional return potential
   - Optimize for maximum expected return per unit of capital risk
   - Account for correlation between positions

2. **THETA = Cost** 💸
   - Daily time decay as the "cost" of holding the portfolio
   - Track theta burn rate vs expected moves needed to break-even
   - Optimize positions to minimize unnecessary theta while maintaining exposure

3. **DIVERSIFICATION SCORE = Risk Balance** ⚖️
   - Measure concentration risk across symbols, sectors, expiration dates
   - Penalize over-concentration in single names or timeframes
   - Reward well-balanced exposure that maintains return potential

#### Phase 2: Enhanced Calculations
- **Dynamic beta calculation** from rolling price correlations
- **Improved stress testing** using historical market scenarios
- **Portfolio optimization suggestions** based on the 3-metric framework
- **Real-time alerts** when metrics exceed risk thresholds

#### Phase 3: Advanced Features
- **Earnings calendar integration** for theta acceleration awareness
- **Implied volatility rank** tracking for better entry/exit timing
- **Sector rotation analysis** for diversification optimization
- **Paper trading mode** for strategy backtesting

### Development Approach
When resuming development:
1. **Validate current calculations** against real portfolio performance
2. **Define precise formulas** for the 3-metric optimization framework
3. **Implement incremental improvements** with A/B testing against actual results
4. **Focus on actionable insights** rather than theoretical metrics

### Success Metrics
The enhanced system should help answer:
- "Is my portfolio efficiently positioned for expected returns vs costs?"
- "Am I properly diversified to handle unexpected market moves?"
- "What adjustments would improve my risk-adjusted return potential?"

This foundation provides excellent infrastructure for building a sophisticated portfolio optimization tool focused on practical trading decisions.