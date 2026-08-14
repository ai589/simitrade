# Round 4: candidate strategies at a 10-trading-day (~14 calendar day) hold.
# Same 5y framework, rebalancing every 10 days. Sharpe annualized at sqrt(26).
# Winner gets promoted into screener.py as the 14-day strategy.
#
# Usage:  python variants3.py

import json
import math
import time

from backtest import features, TOP_N, COST, NONSTOCK
from variants import load_data, VR
from screener import SECTOR_OF

HOLD = 10
LOOKBACK = 70


def stats10(periods, spy_periods):
    n = len(periods)
    wins = sum(1 for w in periods if w > 0)
    mean = sum(periods) / n
    sd = math.sqrt(sum((w - mean) ** 2 for w in periods) / n) or 1e-9
    eq, peak, mdd = 1.0, 1.0, 0.0
    for w in periods:
        eq *= 1 + w
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    half = n // 2
    return {
        "weeks": n, "hitRate": round(wins / n * 100, 1),
        "avgWeek": round(mean * 100, 3),          # avg per 2-week period
        "sharpe": round(mean / sd * math.sqrt(26), 2),
        "maxDD": round(mdd * 100, 1),
        "total": round((eq - 1) * 100, 1),
        "worstWeek": round(min(periods) * 100, 2),
        "bestWeek": round(max(periods) * 100, 2),
        "beatSpyPct": round(sum(1 for w, s in zip(periods, spy_periods) if w > s) / n * 100, 1),
        "avgWeekH1": round(sum(periods[:half]) / half * 100, 3),
        "avgWeekH2": round(sum(periods[half:]) / (n - half) * 100, 3),
    }


def main():
    data = load_data()
    print(f"Universe: {len(data) - 1} names + SPY (cached). Hold = {HOLD} trading days.")

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
    rebalances = list(range(LOOKBACK, len(cal) - HOLD, HOLD))
    print(f"{len(rebalances)} two-week rebalances.")

    def uptrend(feats):
        return [(s, f) for s, f in feats.items()
                if f["px"] > f["sma20"] > f["sma50"] and (f["rsi14"] or 0) <= 75]

    def by_sector(pool):
        groups = {}
        for s, f in pool:
            groups.setdefault(SECTOR_OF.get(s.replace("-", "."), "Other"), []).append((s, f))
        return groups

    VARIANTS = {
        "14d mom top5": (lambda f: [s for s, _ in sorted(uptrend(f),
            key=lambda p: -(p[1]["ret60"] + p[1]["ret20"]))[:TOP_N]], False),
        "14d mom top10 + regime": (lambda f: [s for s, _ in sorted(uptrend(f),
            key=lambda p: -(p[1]["ret60"] + p[1]["ret20"]))[:10]], True),
        "14d sector-neutral + regime": (lambda f: [
            max(g, key=lambda p: p[1]["ret60"] + p[1]["ret20"])[0]
            for g in by_sector(uptrend(f)).values()], True),
        "14d weekly dip": (lambda f: [s for s, x in sorted(
            [(s, x) for s, x in f.items() if x["px"] > x["sma50"] and x["sma20"] > x["sma50"]],
            key=lambda p: p[1]["ret5"])[:TOP_N]], False),
        "14d lowvol + regime": (lambda f: [s for s, x in sorted(
            [(s, x) for s, x in f.items() if x["px"] > x["sma20"] and x["sma20"] > x["sma50"]],
            key=lambda p: p[1]["vol20"])[:TOP_N]], True),
        "14d up-day consistency + regime": (lambda f: [s for s, x in sorted(uptrend(f),
            key=lambda p: -p[1]["upFrac"])[:TOP_N]], True),
    }

    periods = {k: [] for k in VARIANTS}
    spy_periods = []

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
        spy_periods.append(spy_closes[t + HOLD] / spy_closes[t] - 1)

        for key, (fn, gated) in VARIANTS.items():
            if gated and not regime_on:
                periods[key].append(0.0)
                continue
            picks = fn(feats)
            if picks:
                rets = [aligned[s][0][t + HOLD] / aligned[s][0][t] - 1 - COST for s in picks]
                periods[key].append(sum(rets) / len(rets))
            else:
                periods[key].append(0.0)

    spy_stats = stats10(spy_periods, spy_periods)
    print(f"\nSPY same period: avg {spy_stats['avgWeek']}%/2wk, sharpe {spy_stats['sharpe']},"
          f" maxDD {spy_stats['maxDD']}%, total {spy_stats['total']}%\n")
    print(f"{'variant':<34}{'hit%':>6}{'avg2wk':>8}{'sharpe':>8}{'maxDD':>8}"
          f"{'total':>8}{'H1':>8}{'H2':>8}")
    results = {}
    for key in VARIANTS:
        st = stats10(periods[key], spy_periods)
        results[key] = st
        print(f"{key:<34}{st['hitRate']:>6}{st['avgWeek']:>8}{st['sharpe']:>8}"
              f"{st['maxDD']:>8}{st['total']:>8}{st['avgWeekH1']:>8}{st['avgWeekH2']:>8}")

    with open(VR, encoding="utf-8") as f:
        merged = json.load(f)
    merged["variants14"] = results
    merged["spy14"] = spy_stats
    # period-return series for the dashboard equity chart
    dates = [time.strftime("%Y-%m-%d", time.gmtime(cal[t] * 86400)) for t in rebalances]
    merged["curves14"] = {"dates": dates}
    for key in VARIANTS:
        merged["curves14"][key] = [round(w, 5) for w in periods[key]]
    with open(VR, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=1)
    print("\nMerged into variants_results.json (variants14 + curves14)")


if __name__ == "__main__":
    main()
