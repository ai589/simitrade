# Am I late to the game? — evidence, September 2026

The question: signals come from a US close (04:00 SGT), the trader is awake 09:00–18:00 SGT,
and the US market is shut that entire window. Is the edge gone by the time an order can be
placed?

Short answer: **no.** The delay is not the problem. Strategy selection was, and the
automation being dead for four days was.

Reproduce with `python src/delay_test.py` and `python src/stop_test.py`
(→ `state/delay_test.json`, `state/stop_test.json`).

---

## 1. Waiting longer costs nothing

`src/delay_test.py`, 448 weekly rebalances, 2017-09-18 → 2026-08-11, 540 names,
point-in-time S&P membership, 0.10% round-trip cost. Same signal, entry pushed back
N sessions, **same holding period**. Avg % per rebalance:

| strategy | d0 | d1 | d2 | d3 | change |
|---|---|---|---|---|---|
| momTop10 | 0.299 | 0.321 | 0.345 | 0.416 | **+0.117** |
| momTop5 | 0.523 | 0.502 | 0.471 | 0.446 | −0.077 |
| sectorNeutral | 0.194 | 0.230 | 0.311 | 0.363 | **+0.169** |
| weeklyDip | 0.256 | 0.140 | 0.168 | 0.229 | −0.027 |
| valueDD | 0.631 | 0.570 | 0.701 | 0.751 | **+0.120** |
| value200 | 0.343 | 0.388 | 0.446 | 0.420 | +0.077 |
| mom14 † | 1.105 | 1.116 | 1.118 | 1.128 | +0.023 |
| sector14 † | 0.534 | 0.592 | 0.621 | 0.611 | +0.077 |

† 10-session hold; the rest hold 5. Not directly comparable to the 5-day rows — roughly
halve them for a per-week figure.

Nothing decays. Most strategies are *better* three sessions late. A one-session delay
(what actually happens: signal at the close, fill at the next open) is a rounding error.

**The one delay that does cost:** if you enter late but keep the original sell-by date,
you just hold for fewer days and earn less — valueDD −0.416, value200 −0.343, mom14 −0.249
across d0→d3. So **if you are late, shift the exit too.** Do not keep the original
sell-by.

## 2. Nothing lives in the untradeable overnight gap

Delay-0 return split into `close[t] → open[t+1]` (the gap, impossible to trade from
Singapore) and `open[t+1] → exit`:

| strategy | gap % | rest % | full % | gap share |
|---|---|---|---|---|
| momTop10 | 0.042 | 0.508 | 0.555 | 8% |
| weeklyDip | 0.063 | 0.356 | 0.424 | 15% |
| valueDD | −0.058 | 0.731 | 0.706 | **−8%** |
| value200 | −0.027 | 0.433 | 0.407 | **−7%** |
| mom14 | 0.036 | 1.195 | 1.247 | 3% |

Buying at the next open captures 85–100% of the move. For the two value strategies the
gap is *negative* — the overnight drift goes against the position before you buy, so
waiting gets you a better price. Being asleep for the US session is not costing anything.

## 3. The kill switch was comparing two different things

`strategy_health()` judged the live paper log against `variants_results.json`. They were
built on different rules:

| | entry | exit | stops |
|---|---|---|---|
| live paper log | `close[t]` | `close[t+hold]` | 1.5×/2× ATR, stop checked before target |
| variants\*.py backtest | `open[t+1]` | `open[t+1+hold]` | none |

`src/stop_test.py` runs the same pickers under all four combinations. Gross % per trade,
449 rebalances:

| strategy | close+stops (= live rules) | close, no stops | nextopen, no stops (= old benchmark) | LIVE paper | live − model |
|---|---|---|---|---|---|
| momTop10 | 0.204 | 0.469 | 0.500 | 0.027 | −0.177 |
| momTop5 | 0.336 | 0.606 | 0.610 | −0.399 | −0.735 |
| lowvol | 0.086 | 0.053 | 0.090 | −0.464 | −0.550 |
| sectorNeutral | 0.139 | 0.318 | 0.363 | 0.063 | −0.076 |
| **weeklyDip** | 0.283 | 0.430 | 0.366 | **1.576** | **+1.293** |
| **valueDD** | 0.696 | 0.753 | 0.755 | **2.081** | **+1.385** |
| **value200** | 0.333 | 0.370 | 0.429 | **1.413** | **+1.080** |
| zblend | 0.143 | 0.354 | 0.414 | −0.384 | −0.527 |
| mom14 | 0.690 | 1.146 | 1.189 | −0.535 | −1.225 |
| sector14 | 0.446 | 0.757 | 0.814 | 0.114 | −0.332 |

Two things fall out:

**The 1.5× ATR stop is expensive, and much more so for momentum than for mean-reversion.**
momTop10 loses 57% of its return to the stop (0.469 → 0.204), mom14 40%, zblend 60%.
valueDD loses 8%, value200 10%. Momentum names are volatile and whipsaw through a tight
stop; the value names drift.

**But the demotions were not an artefact.** Fixing the benchmark did not rescue the
momentum baskets — they are demoted because their *recent 60 live trades* are negative
(momTop10 −1.67%/trade, sectorNeutral −1.74%, mom14 −3.66%), which is independent of what
they are benchmarked against. The corrected benchmark makes the `watch` threshold and the
live/backtest ratio honest; it does not change the verdicts.

Simulated exit mix under the live rules (27–34% stopped, 16–20% target, 46–57% time-exit)
closely matches the real paper log (26.4% / 15.9% / 57.7%), so the simulation is faithful.

`strategy_health()` now prefers `state/stop_test.json` and records which basis it used in
`health.btBasis`.

## 4. What changed as a result

- `push_orders.py` trades `["valueDD", "weeklyDip", "value200"]`, was
  `["mom14", "sector14", "valueDD"]`. mom14 is the worst performer in the paper log.
- `never_trade` hard-blocks the four short baskets (−0.77% to −3.15%/trade live, negative
  in backtest, borrow fees not even modelled).
- Position-sizing bug fixed: the regime multiplier and portfolio-heat scale were applied
  *before* the max-position cap, so the cap put the size straight back up and both
  multipliers were silently cancelled whenever it was binding. Six names at a 3% heat cap
  still risked 3.6%. Fixed in `push_orders.shares()` and `dashboard.html shares()`;
  regression tests in `tests/test_sizing.py`.

## 5. Caveats

- The live paper log is two months (2026-07-06 → 09-03) in one regime, 139–372 closed
  trades per strategy. The +1.0 to +1.4%/trade mean-reversion outperformance is large but
  the sample is short and regime-specific. Require agreement between the corrected
  backtest and the live log before promoting or demoting anything.
- Point-in-time masking covers S&P additions only; Nasdaq-100-only names and the pinned
  ADR extras keep full history, so residual survivorship bias remains.
- No slippage beyond 10 bps round trip, and no real fill has ever been recorded — not one
  order has gone through `push_orders.py`, on paper or live. Until real fills are logged
  back into `picks_log.json`, actual slippage is unmeasured.
- Backtest and paper log both exclude names with earnings inside the hold window; the
  10-day strategies use a 14-day exclusion.
