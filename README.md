# US Swing Trade Dashboard (NYSE + Nasdaq)

A self-contained swing-trading research dashboard for liquid NYSE and Nasdaq large-caps. It screens ~200 names daily, runs 11 backtested strategies (momentum, mean-reversion, value proxies, and bear-market shorts) across 1-week and 2-week horizons, and tracks every pick in an automatic paper-trade record — so you can see whether live results match the backtest **before** risking money.

![Dashboard](https://img.shields.io/badge/stack-Python%20%2B%20vanilla%20JS-blue) ![License](https://img.shields.io/badge/license-MIT-green)

## What it does

- **Auto-refresh 3× per trading day** — 8:00 AM SGT plus ~10-15 min after the US open and close via Windows scheduled tasks; `market_guard.py` converts to America/New_York time so the schedule survives US daylight-saving flips and skips weekends
- **Daily screen** of ~200 liquid NYSE and Nasdaq names (>$50M/day traded) on momentum, trend, RSI, volume, volatility and 52-week value signals
- **11 strategies, all backtested 10 years** (2016–2026, weekly rebalance, trading costs included) with honest stats: hit rate, Sharpe, max drawdown, growth of $10,000
- **Regime filter** — strategies sit in cash when SPY is below its 50-day MA (shorts activate only *below* it)
- **Earnings filter** — names reporting within the hold window are excluded from picks
- **ATR-based levels** — every pick gets a stop and target scaled to its own volatility, with % distances
- **Position sizing** — risk-% based share counts, capped by account %, and an optional total budget split across each basket
- **Paper-trade record** — every refresh logs picks and scores matured ones against real prices (stop → target → time exit), side by side with an SPY benchmark
- **Zoomable 10-year chart** — strategy equity curves vs SPY, gold and oil, with economic-cycle bands (COVID, 2022 bear, Fed hiking/cutting), rebased to $10k at any zoom start

## Quick start

```
pip install requests
python src/screener.py      # pulls prices, writes data.js  (~1 min)
python src/variants.py      # 10y backtest round 1 (momentum/low-vol)   ─┐
python src/variants2.py     # round 2 (sector-neutral, dips)             │ run once,
python src/variants3.py     # round 3 (14-day holds)                     │ then quarterly
python src/variants4.py     # round 4 (shorts, value proxies)            │
python src/variants5.py     # round 5 (blends; merges after round 4)    ─┘
```

Then open `dashboard.html` in a browser. `refresh.bat` opens the dashboard and refreshes data in one double-click; the page auto-reloads itself when new data lands. Schedule `refresh_silent.bat` daily (e.g. Windows Task Scheduler at 8:00 AM) and the paper-trade record builds itself.

## Architecture

| Path | Role |
|---|---|
| `dashboard.html` | The entire UI — single file, no build step, no dependencies |
| `data.js` | Generated data payload read by the dashboard (kept at root for Vercel) |
| `src/screener.py` | Daily data pull (Yahoo Finance chart API), scoring, strategy picks, paper-trade log |
| `src/backtest.py` | Shared backtest engine + 10-year data fetch |
| `src/variants*.py` | Strategy research rounds; results merge into `state/variants_results.json` |
| `src/market_guard.py` | US market-hours gate for the open/close scheduled refresh |
| `state/` | Mutable state: paper-trade history (`picks_log.json`), backtest results, price/earnings caches |
| `logs/` | `refresh_log.txt` from the scheduled refreshes |
| `archive/` | Superseded files kept for reference (legacy `strategies.json`) |
| `refresh*.bat`, `publish.bat` | Entry points: manual/scheduled refresh, publish to GitHub + Vercel |

## Honest findings from the backtests

- Only **one** strategy beat SPY on both return and drawdown over 10 years: 14-day sector-neutral momentum with a regime filter (Sharpe 1.01, −15% max DD)
- **Every short strategy tested lost money** — five configurations, all negative, before borrow fees
- Buying dips works at a 5-day horizon and **fails at 14 days**; momentum survives longer holds
- The regime filter's entire value is concentrated in bear markets (2020, 2022) — it costs return the rest of the time
- Survivorship bias: the universe is today's constituents, so absolute returns are flattered; treat rankings as the reliable output

## Disclaimer

This is a research and educational tool, **not investment advice**. Backtested performance does not predict future returns. A 1–2 week horizon is dominated by noise and news. If you trade with it: paper-trade first, size small, always use the stops, and never risk money you cannot afford to lose.
