# GEX Levels Tool — Design

Date: 2026-09-30
Status: approved in chat ("go baby"); user asked for autonomous build with a commit per milestone.

## Goal

One command (`python gex.py ES` / `python gex.py NQ`) that turns free CBOE option data into
dealer gamma levels a discretionary ES/NQ futures trader can put on a TradingView chart before the open.

Non-goal: seeing real dealer positions (not public). Levels are an estimate from open interest,
using the standard assumption that dealers are long calls and short puts.

## Inputs

| Product | Index chain | ETF chain | Futures (yfinance) | Index (yfinance) | Strike bin |
|---|---|---|---|---|---|
| ES | `_SPX` | `SPY` | `ES=F` | `^GSPC` | 5 |
| NQ | `_NDX` | `QQQ` | `NQ=F` | `^NDX` | 10 |

- URL: `https://cdn.cboe.com/api/global/delayed_quotes/options/{ticker}.json` (redirects to `cdn-api`).
  Index tickers need `_`, ETFs must not have it.
- `timestamp` is UTC. Option symbol: `ROOT YYMMDD C|P STRIKE*1000 (8 digits)`.
- Expiry time (America/New_York): 16:00, except AM-settled monthly roots `SPX`, `NDX` → 09:30.
  Options already expired at the data timestamp are dropped. Rows with `open_interest <= 0` or `iv <= 0` are dropped.
- Data is fetched fresh every run (no stale cache). `--from-dir DIR` reads `DIR/{ticker}.json` instead (offline / tests).

## Combining index + ETF

ETF options are converted to index-equivalent units with λ = index_spot / etf_spot:
strike × λ, contract multiplier 100 / λ. This keeps each option's dollar gamma unchanged.

## Math

- Black-Scholes gamma, r = q = 0, own gamma from CBOE `iv` (CBOE `gamma` is often 0).
- T in years = max(hours to expiry, 1) / (365 × 24).
- Dollar gamma per 1% move = γ × OI × multiplier × S² × 0.01. Calls +, puts −.

## Levels (index points, then shifted by basis to futures points, rounded to 0.25)

| Level | Definition |
|---|---|
| Total GEX | Net dollar gamma at spot, $bn per 1% |
| Gamma flip | Zero crossing of the net GEX profile (±10% of spot, 201 steps) closest to spot; `None` if no crossing |
| Regime | Positive if total GEX at spot > 0, else negative |
| Call wall | Strike bin with most positive net GEX (±15% of spot) |
| Put wall | Strike bin with most negative net GEX (±15% of spot) |

Walls use net GEX because round strikes (e.g. SPX 8000) carry huge offsetting call and put OI;
counting calls and puts separately made one strike both walls on live data.
| Next-expiry call/put wall | Same, using only the nearest live expiry (0DTE on trading days) |
| Magnets | Top 3 strike bins by abs(net GEX) within ±2% of spot, excluding the two walls |
| Expected move | Spot ± ATM straddle mid of nearest index expiry; fallback spot × iv30 / √252 |

## Basis (index → futures)

yfinance 1-minute closes for futures and index over the last 5 days, last common timestamp,
basis = fut − index. `--basis X` overrides. If neither works: warn and show index levels only (basis 0).

## Outputs per run

1. Terminal table: level, index price, futures price, distance from spot, meaning + regime playbook.
2. PNG chart `output/{PRODUCT}_{YYYY-MM-DD}.png`: GEX by strike + profile, levels marked, in futures points.
3. TradingView string `7710.25=Call Wall;7605=Put Wall;...` printed and saved to `output/{PRODUCT}_tradingview.txt`,
   read by a one-time indicator `tradingview/gex_levels.pine`.
4. One row appended to `history/levels_history.csv`.

## Layout

```
gex/config.py    products
gex/data.py      fetch + parse CBOE
gex/greeks.py    vectorized gamma
gex/levels.py    all level math
gex/futures.py   basis
gex/output.py    table, chart, TradingView string, history CSV
gex.py           CLI
tests/           pytest; tests/fixtures/NDX.json (2024-04-03 real chain)
legacy/          old scripts and CSVs
```

## Error handling

- Network/HTTP failure on the index chain → exit with a clear message. ETF chain failure → warn, continue index-only.
- No flip in range → shown as "none in ±10%", regime from sign of total GEX.
- yfinance failure → warning, index-only levels.

## Testing

Unit tests for gamma math, symbol parsing/expiry filtering, ETF scaling invariance, walls/flip on synthetic chains,
basis alignment, TradingView string, history append. Integration test runs the full pipeline on the 2024 NDX fixture.
