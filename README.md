# ECL - trade - main

A US large-cap swing-trade screener, backtester and paper-trade tracker, published as a
static dashboard: **https://simitrade.vercel.app**

Daily bars from Yahoo Finance for the S&P 500 + Nasdaq-100 + Dow 30 + 21 pinned ADRs
(~534 names). The dashboard leads with **Top Buys** and **Top Sells** for the next 5 to 10
trading days. Each pick's score has three visible parts:

- **setup** - the short-term price pattern (dip in an uptrend, value turning up,
  breakdown, blow-off...)
- **news** - recent headlines for the stock, read and scored by Claude
- **macro** - sector strength plus a risk-appetite score built from the S&P trend,
  breadth, VIX, credit, yields and the dollar

Below that, a "Strategy lab" keeps the 13 older rule-based baskets, their 10-year
backtests and the paper-trade record. Every pick is logged and marked to market, so the
live record can be compared with the backtest.

### News needs an API key

The news read uses the Anthropic API. Put a key in `state/anthropic_key.txt` (one line),
or set `ANTHROPIC_API_KEY`. Without a key the tool still runs but scores headlines with a
crude keyword list, and says so at the top of the dashboard and the brief.

## Run it

| | |
|---|---|
| `1. refresh.bat` | pull fresh prices, rebuild `data.js`, open the dashboard |
| `2. publish.bat` | mirror to GitHub and deploy to Vercel |
| `3. push_orders.bat` | dry-run this week's bracket orders (`--check` to test the broker connection, `--paper` / `--live` to send) |
| `morning_brief.bat` | today's brief → `output/brief-YYYY-MM-DD.txt` + WhatsApp self-chat |
| `tools\install_tasks.ps1` | (re)register the scheduled tasks at the current path |

```
python -m pytest tests/ -q          # indicators, sizing, exits, pick ranking, macro, news read
python src/picks_backtest.py        # 10y backtest of the price-setup part of the two lists
python src/delay_test.py            # does acting late cost anything?
python src/stop_test.py             # what the ATR stop costs, per strategy
python src/variants.py              # the production backtest

python src/whatsapp_send.py --login # one-off: log the brief's Chrome profile into WhatsApp
python src/whatsapp_send.py --check # is that session still live?
python src/morning_brief.py --whatsapp   # phone-formatted brief, sends nothing
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

Broker setup and the paper-trading checklist:
[`docs/tiger-paper-setup.md`](docs/tiger-paper-setup.md).

## Honest numbers

Over 488 weekly rebalances (10y, next-open fills, point-in-time S&P membership, 0.10%
round-trip cost) **no strategy here beat simply holding SPY** on risk-adjusted terms —
SPY returned +252% at Sharpe 0.89 and a −29.2% max drawdown. Treat these as a satellite
around a core holding, not as a portfolio.

The news and macro overlays on Top Buys / Top Sells **have no backtest** - there are no
historical headlines to replay. Only the price-setup part is backtested. Every short rule
tested here lost money, so Top Sells is a list to avoid, trim or hedge, and the order
pusher will not short it.

Not investment advice.
