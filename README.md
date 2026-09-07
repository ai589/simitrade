# ECL - trade - main

A US large-cap swing-trade screener, backtester and paper-trade tracker, published as a
static dashboard: **https://simitrade.vercel.app**

Daily bars from Yahoo Finance for the S&P 500 + Nasdaq-100 + Dow 30 + 21 pinned ADRs
(~534 names), scored into 13 strategy baskets with ATR-based stops and targets, held 5 or
10 trading days. Every pick is logged and marked to market, so the live record can be
compared with the backtest.

## Run it

| | |
|---|---|
| `1. refresh.bat` | pull fresh prices, rebuild `data.js`, open the dashboard |
| `2. publish.bat` | mirror to GitHub and deploy to Vercel |
| `3. push_orders.bat` | dry-run this week's bracket orders (add `--paper` / `--live` to send) |
| `morning_brief.bat` | today's brief → `output/brief-YYYY-MM-DD.txt` |
| `tools\install_tasks.ps1` | (re)register the scheduled tasks at the current path |

```
python -m pytest tests/ -q          # 62 tests over the indicator, sizing and exit logic
python src/delay_test.py            # does acting late cost anything?
python src/stop_test.py             # what the ATR stop costs, per strategy
python src/variants.py              # the production backtest
```

## Layout

```
src/        screener, backtest rounds (variants*.py), order pusher, brief, analysis
state/      caches, paper-trade log, backtest results   (gitignored)
tools/      install_tasks.ps1
docs/       findings worth keeping
tests/      pytest suite
output/     generated briefs
logs/       refresh / publish / brief logs
```

## Timing

The US session is 21:30–04:00 SGT, so signals land overnight and orders are placed the
next SGT daytime as limit-with-bracket orders that rest until the US open. The delay this
implies costs nothing — see [`docs/delay-and-stops-2026-09.md`](docs/delay-and-stops-2026-09.md).

## Honest numbers

Over 488 weekly rebalances (10y, next-open fills, point-in-time S&P membership, 0.10%
round-trip cost) **no strategy here beat simply holding SPY** on risk-adjusted terms —
SPY returned +252% at Sharpe 0.89 and a −29.2% max drawdown. Treat these as a satellite
around a core holding, not as a portfolio.

Not investment advice.
