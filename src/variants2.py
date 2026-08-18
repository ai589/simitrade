# Round 3: a NEW family of strategy candidates over the same 5y weekly framework.
# Winners get promoted into screener.py / dashboard.html alongside round-2 picks.
# Uses the px5y_cache.json data cache; merges results into variants_results.json.
#
# Usage:  python variants2.py

import json
import math
import time

from backtest import features, stats, TOP_N, HOLD, COST, NONSTOCK
from variants import load_data, align, hold_ret, VR
from screener import SECTOR_OF

LOOKBACK = 70
LONG_LB = 260  # for 52-week-high proximity


def main():
    data = load_data()
    print(f"Universe: {len(data) - 1} names + SPY (cached).")

    cal, aligned = align(data)

    spy_closes = aligned["SPY"][0]
    # start rebalances at LONG_LB so all strategies share identical weeks
    rebalances = list(range(LONG_LB, len(cal) - HOLD, HOLD))
    print(f"{len(rebalances)} weekly rebalances (from bar {LONG_LB} so the 52w-high "
          "strategy has history; slightly shorter sample than round 2).")

    def zrank(pool, key):
        vals = [key(f) for _, f in pool]
        m = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals)) or 1e-9
        return {s: (key(f) - m) / sd for (s, f), v in zip(pool, vals)}

    def uptrend(feats):
        return [(s, f) for s, f in feats.items()
                if f["px"] > f["sma20"] > f["sma50"] and (f["rsi14"] or 0) <= 75]

    VARIANTS = {
        "sector-neutral mom + regime": lambda f, s: [
            max(group, key=lambda p: p[1]["ret60"] + p[1]["ret20"])[0]
            for group in _by_sector(uptrend(f)).values()],
        "52w-high proximity + regime": lambda f, s: [
            sym for sym, x in sorted(
                [(sym, x) for sym, x in f.items()
                 if x["px"] > x["sma50"] and x.get("hi252")],
                key=lambda p: -(p[1]["px"] / p[1]["hi252"]))[:TOP_N]],
        "weekly dip in uptrend": lambda f, s: [
            sym for sym, x in sorted(
                [(sym, x) for sym, x in f.items()
                 if x["px"] > x["sma50"] and x["sma20"] > x["sma50"]],
                key=lambda p: p[1]["ret5"])[:TOP_N]],
        "weekly dip + regime": lambda f, s: [
            sym for sym, x in sorted(
                [(sym, x) for sym, x in f.items()
                 if x["px"] > x["sma50"] and x["sma20"] > x["sma50"]],
                key=lambda p: p[1]["ret5"])[:TOP_N]],
        "up-day consistency + regime": lambda f, s: [
            sym for sym, x in sorted(uptrend(f),
                key=lambda p: -p[1]["upFrac"])[:TOP_N]],
        "volume-confirmed mom + regime": lambda f, s: [
            sym for sym, x in sorted(
                [(sym, x) for sym, x in uptrend(f) if x["volratio"] >= 1.0],
                key=lambda p: -(p[1]["ret60"] + p[1]["ret20"]))[:TOP_N]],
        "mom/low-vol z-blend + regime": lambda f, s: _zblend(f),
    }
    REGIME_GATED = {k for k in VARIANTS if "+ regime" in k}

    def _by_sector(pool):
        groups = {}
        for s, f in pool:
            groups.setdefault(SECTOR_OF.get(s.replace("-", "."), "Other"), []).append((s, f))
        return groups

    def _zblend(f):
        pool = [(s, x) for s, x in f.items() if x["px"] > x["sma50"]]
        if not pool:
            return []
        zm = zrank(pool, lambda x: x["ret60"])
        zv = zrank(pool, lambda x: x["vol20"])
        return [s for s, _ in sorted(pool, key=lambda p: -(zm[p[0]] - zv[p[0]]))[:TOP_N]]

    weekly = {k: [] for k in VARIANTS}
    spy_weekly = []

    for t in rebalances:
        feats = {}
        for sym, (cl, hi, vo, first, _op) in aligned.items():
            if sym in NONSTOCK or first > t - LOOKBACK:
                continue
            f = features(cl[t - LOOKBACK + 1: t + 1],
                         hi[t - LOOKBACK + 1: t + 1],
                         vo[t - LOOKBACK + 1: t + 1])
            w = cl[t - LOOKBACK + 1: t + 1]
            f["upFrac"] = sum(1 for i in range(len(w) - 60, len(w))
                              if w[i] > w[i - 1]) / 60
            if first <= t - LONG_LB:
                f["hi252"] = max(hi[t - LONG_LB + 1: t + 1])
            feats[sym] = f
        spy_win = spy_closes[t - LOOKBACK + 1: t + 1]
        spyf = features(spy_win, spy_win, [0] * LOOKBACK)
        regime_on = spyf["px"] > spyf["sma50"]
        spy_weekly.append(hold_ret(aligned, "SPY", t, HOLD))

        for key, fn in VARIANTS.items():
            if key in REGIME_GATED and not regime_on:
                weekly[key].append(0.0)
                continue
            picks = fn(feats, spyf)
            if picks:
                rets = [hold_ret(aligned, s, t, HOLD) - COST
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

    # merge into variants_results.json (keep round-2 results and curves)
    with open(VR, encoding="utf-8") as f:
        merged = json.load(f)
    merged["variants"].update(results)
    dates = [time.strftime("%Y-%m-%d", time.gmtime(cal[t] * 86400)) for t in rebalances]
    merged.setdefault("curves2", {})["dates"] = dates
    for key in VARIANTS:
        merged["curves2"][key] = [round(w, 5) for w in weekly[key]]
    merged["curves2"]["spy"] = [round(w, 5) for w in spy_weekly]
    with open(VR, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=1)
    print("\nMerged into variants_results.json")


if __name__ == "__main__":
    main()
