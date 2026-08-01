# Round 2: refined strategy variants over the same 5y weekly framework.
# Adds: SPY regime filter (cash when SPY < SMA50), skip-recent-week momentum,
# risk-adjusted momentum, and top-N concentration tests.
# Caches raw 5y data in px5y_cache.json so re-runs are instant.
#
# Usage:  python variants.py

import json
import math
import os
import time
import concurrent.futures

from backtest import fetch5y, features, stats, UNIVERSE, TOP_N, HOLD, LOOKBACK, COST, NONSTOCK

CACHE = "px10y_cache.json"


def load_data():
    if os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < 86400:
        with open(CACHE, encoding="utf-8") as f:
            raw = json.load(f)
        return {s: {int(k): tuple(v) for k, v in d.items()} for s, d in raw.items()}
    syms = UNIVERSE + sorted(NONSTOCK)
    print(f"Fetching 10y history for {len(syms)} symbols...")
    data = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(fetch5y, syms):
            if res:
                data[res[0]] = res[1]
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def rank_uptrend(feats, key, n):
    c = [(s, f) for s, f in feats.items()
         if f["px"] > f["sma20"] > f["sma50"] and (f["rsi14"] or 0) <= 75]
    c.sort(key=lambda x: -key(x[1]))
    return [s for s, _ in c[:n]]


VARIANTS = {
    # label: (picker(feats, spyf) -> list, regime_filtered)
    "mom (baseline)":      (lambda f, s: rank_uptrend(f, lambda x: x["ret60"] + x["ret20"], TOP_N), False),
    "mom + regime":        (lambda f, s: rank_uptrend(f, lambda x: x["ret60"] + x["ret20"], TOP_N), True),
    "mom skip-week":       (lambda f, s: rank_uptrend(f, lambda x: x["ret60"] - x["ret5"], TOP_N), False),
    "mom skip + regime":   (lambda f, s: rank_uptrend(f, lambda x: x["ret60"] - x["ret5"], TOP_N), True),
    "mom risk-adj":        (lambda f, s: rank_uptrend(f, lambda x: x["ret60"] / max(x["vol20"], 1e-4), TOP_N), False),
    "mom risk-adj + regime": (lambda f, s: rank_uptrend(f, lambda x: x["ret60"] / max(x["vol20"], 1e-4), TOP_N), True),
    "mom top3 + regime":   (lambda f, s: rank_uptrend(f, lambda x: x["ret60"] + x["ret20"], 3), True),
    "mom top10 + regime":  (lambda f, s: rank_uptrend(f, lambda x: x["ret60"] + x["ret20"], 10), True),
    "relstr + regime":     (lambda f, s: sorted(
        [(sy, x) for sy, x in f.items() if x["px"] > x["sma50"]],
        key=lambda p: -(p[1]["ret60"] - s["ret60"]))[:TOP_N] and
        [sy for sy, _ in sorted([(sy, x) for sy, x in f.items() if x["px"] > x["sma50"]],
                                key=lambda p: -(p[1]["ret60"] - s["ret60"]))[:TOP_N]], True),
    "lowvol + regime":     (lambda f, s: [sy for sy, _ in sorted(
        [(sy, x) for sy, x in f.items() if x["px"] > x["sma20"] and x["sma20"] > x["sma50"]],
        key=lambda p: p[1]["vol20"])[:TOP_N]], True),
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
    rebalances = list(range(LOOKBACK, len(cal) - HOLD, HOLD))

    weekly = {k: [] for k in VARIANTS}
    exposure = {k: 0 for k in VARIANTS}
    spy_weekly = []

    for t in rebalances:
        feats = {}
        for sym, (cl, hi, vo, first) in aligned.items():
            if sym in NONSTOCK or first > t - LOOKBACK:
                continue
            feats[sym] = features(cl[t - LOOKBACK + 1: t + 1],
                                  hi[t - LOOKBACK + 1: t + 1],
                                  vo[t - LOOKBACK + 1: t + 1])
        spy_win = spy_closes[t - LOOKBACK + 1: t + 1]
        spyf = features(spy_win, spy_win, [0] * LOOKBACK)
        spy_regime_on = spyf["px"] > spyf["sma50"]
        spy_weekly.append(spy_closes[t + HOLD] / spy_closes[t] - 1)

        for key, (fn, regime) in VARIANTS.items():
            if regime and not spy_regime_on:
                weekly[key].append(0.0)  # cash
                continue
            picks = fn(feats, spyf)
            if picks:
                rets = [aligned[s][0][t + HOLD] / aligned[s][0][t] - 1 - COST for s in picks]
                weekly[key].append(sum(rets) / len(rets))
                exposure[key] += 1
            else:
                weekly[key].append(0.0)

    spy_stats = stats(spy_weekly, spy_weekly)
    n = len(rebalances)
    print(f"\n{n} weeks | SPY: avg {spy_stats['avgWeek']}%/wk, sharpe {spy_stats['sharpe']},"
          f" maxDD {spy_stats['maxDD']}%, total {spy_stats['total']}%\n")
    print(f"{'variant':<24}{'hit%':>6}{'avg wk':>8}{'sharpe':>8}{'maxDD':>8}"
          f"{'total':>8}{'H1 wk':>8}{'H2 wk':>8}{'expo%':>7}")
    results = {}
    for key in VARIANTS:
        st = stats(weekly[key], spy_weekly)
        st["exposurePct"] = round(exposure[key] / n * 100, 1)
        results[key] = st
        print(f"{key:<24}{st['hitRate']:>6}{st['avgWeek']:>8}{st['sharpe']:>8}"
              f"{st['maxDD']:>8}{st['total']:>8}{st['avgWeekH1']:>8}{st['avgWeekH2']:>8}"
              f"{st['exposurePct']:>7}")

    # weekly return series (for dashboard equity curves), plus rebalance dates
    dates = [time.strftime("%Y-%m-%d", time.gmtime(cal[t] * 86400)) for t in rebalances]
    curves = {"dates": dates, "spy": [round(w, 5) for w in spy_weekly]}
    for key in VARIANTS:
        curves[key] = [round(w, 5) for w in weekly[key]]
    # gold and oil benchmark series (same weekly grid)
    for name, bsym in (("gold", "GC=F"), ("oil", "CL=F")):
        if bsym in aligned:
            bc = aligned[bsym][0]
            # skip weeks with missing or non-positive prices (WTI printed -$37 in
            # Apr 2020, which breaks compounding)
            curves[name] = [round(bc[t + HOLD] / bc[t] - 1, 5)
                            if bc[t] and bc[t + HOLD] and bc[t] > 0 and bc[t + HOLD] > 0
                            else 0.0 for t in rebalances]

    # merge, don't overwrite — rounds 3-5 (variants2/3/4.py) store their results
    # in the same file
    merged = {}
    if os.path.exists("variants_results.json"):
        try:
            with open("variants_results.json", encoding="utf-8") as f:
                merged = json.load(f)
        except Exception:
            pass
    merged["spy"] = spy_stats
    merged["variants"] = results
    merged["curves"] = curves
    with open("variants_results.json", "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=1)
    print("\nWrote variants_results.json (merged)")


if __name__ == "__main__":
    main()
