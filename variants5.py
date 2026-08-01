# Round 5: fresh candidates designed to replace the retired shortRally strategy.
# Same weekly framework and rebalance schedule as rounds 3/4 (bar 260 onward), so
# results are directly comparable and curves share curvesExtra's dates.
# The lead candidate extends the best round-3 rule (mom/low-vol z-blend, Sharpe 1.03)
# with a third factor: up-day consistency (fraction of up days over 60 sessions).
#
# Usage:  python variants5.py

import json
import math
import time

from backtest import features, stats, TOP_N, HOLD, COST, NONSTOCK
from variants import load_data

LOOKBACK = 70
LONG_LB = 260  # keep the shared rebalance grid of rounds 3/4


def zrank(pool, key):
    vals = [key(f) for _, f in pool]
    m = sum(vals) / len(vals)
    sd = math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals)) or 1e-9
    return {s: (key(f) - m) / sd for s, f in pool}


def triple_blend(feats):
    pool = [(s, x) for s, x in feats.items() if x["px"] > x["sma50"]]
    if not pool:
        return []
    zm = zrank(pool, lambda x: x["ret60"] + x["ret20"])
    zv = zrank(pool, lambda x: x["vol20"])
    zu = zrank(pool, lambda x: x["upFrac"])
    return [s for s, _ in sorted(pool, key=lambda p: -(zm[p[0]] - zv[p[0]] + zu[p[0]]))[:TOP_N]]


def dip_in_leaders(feats):
    pool = [(s, x) for s, x in feats.items()
            if x["px"] > x["sma50"] and x["sma20"] > x["sma50"]]
    if len(pool) < TOP_N:
        return [s for s, _ in pool]
    leaders = sorted(pool, key=lambda p: -p[1]["ret60"])[:max(TOP_N * 4, len(pool) // 3)]
    return [s for s, _ in sorted(leaders, key=lambda p: p[1]["ret5"])[:TOP_N]]


def smooth_mom(feats):
    pool = [(s, x) for s, x in feats.items()
            if x["px"] > x["sma20"] > x["sma50"] and (x["rsi14"] or 0) <= 75
            and x["upFrac"] >= 0.55]
    return [s for s, _ in sorted(
        pool, key=lambda p: -(p[1]["ret60"] + p[1]["ret20"]) / max(p[1]["vol20"], 1e-4))[:TOP_N]]


VARIANTS = {
    "fable triple-blend + regime": (triple_blend, True),
    "fable triple-blend always-in": (triple_blend, False),
    "fable dip-in-leaders + regime": (dip_in_leaders, True),
    "fable smooth mom + regime": (smooth_mom, True),
}


def main():
    data = load_data()
    print(f"Universe: {len(data) - 1} names + SPY (cached).")

    cal = sorted(data["SPY"].keys())
    aligned = {}
    for sym, series in data.items():
        closes = [None] * len(cal)
        highs = [None] * len(cal)
        vols = [None] * len(cal)
        last = None
        for i, d in enumerate(cal):
            if d in series:
                last = series[d]
            if last:
                closes[i], highs[i], vols[i] = last
        first = next((i for i, c in enumerate(closes) if c is not None), None)
        if first is not None:
            aligned[sym] = (closes, highs, vols, first)

    spy_closes = aligned["SPY"][0]
    rebalances = list(range(LONG_LB, len(cal) - HOLD, HOLD))
    print(f"{len(rebalances)} weekly rebalances (bar {LONG_LB} onward, same grid as rounds 3/4).")

    weekly = {k: [] for k in VARIANTS}
    spy_weekly = []

    for t in rebalances:
        feats = {}
        for sym, (cl, hi, vo, first) in aligned.items():
            if sym in NONSTOCK or first > t - LOOKBACK:
                continue
            f = features(cl[t - LOOKBACK + 1: t + 1],
                         hi[t - LOOKBACK + 1: t + 1],
                         vo[t - LOOKBACK + 1: t + 1])
            w = cl[t - LOOKBACK + 1: t + 1]
            f["upFrac"] = sum(1 for i in range(len(w) - 60, len(w))
                              if w[i] > w[i - 1]) / 60
            feats[sym] = f
        spy_win = spy_closes[t - LOOKBACK + 1: t + 1]
        spyf = features(spy_win, spy_win, [0] * LOOKBACK)
        regime_on = spyf["px"] > spyf["sma50"]
        spy_weekly.append(spy_closes[t + HOLD] / spy_closes[t] - 1)

        for key, (fn, gated) in VARIANTS.items():
            if gated and not regime_on:
                weekly[key].append(0.0)
                continue
            picks = fn(feats)
            if picks:
                rets = [aligned[s][0][t + HOLD] / aligned[s][0][t] - 1 - COST
                        for s in picks]
                weekly[key].append(sum(rets) / len(rets))
            else:
                weekly[key].append(0.0)

    spy_stats = stats(spy_weekly, spy_weekly)
    print(f"\nSPY same period: avg {spy_stats['avgWeek']}%/wk, sharpe {spy_stats['sharpe']},"
          f" maxDD {spy_stats['maxDD']}%, total {spy_stats['total']}%\n")
    print(f"{'variant':<32}{'hit%':>6}{'avg wk':>8}{'sharpe':>8}{'maxDD':>8}"
          f"{'total':>8}{'H1 wk':>8}{'H2 wk':>8}")
    results = {}
    for key in VARIANTS:
        st = stats(weekly[key], spy_weekly)
        results[key] = st
        print(f"{key:<32}{st['hitRate']:>6}{st['avgWeek']:>8}{st['sharpe']:>8}"
              f"{st['maxDD']:>8}{st['total']:>8}{st['avgWeekH1']:>8}{st['avgWeekH2']:>8}")

    # merge (not replace) into variantsExtra/curvesExtra — same dates as round 4
    with open("variants_results.json", encoding="utf-8") as f:
        merged = json.load(f)
    merged.setdefault("variantsExtra", {}).update(results)
    cex = merged.setdefault("curvesExtra", {})
    cex.setdefault("dates", [time.strftime("%Y-%m-%d", time.gmtime(cal[t] * 86400))
                             for t in rebalances])
    for key in VARIANTS:
        cex[key] = [round(w, 5) for w in weekly[key]]
    with open("variants_results.json", "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=1)
    print("\nMerged into variants_results.json (variantsExtra + curvesExtra)")


if __name__ == "__main__":
    main()
