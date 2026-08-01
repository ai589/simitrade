# Round 5: SHORT-side candidates and VALUE-proxy candidates, 5-day hold,
# same 5y weekly framework (starting at bar 260 so 12-month features exist).
# Short returns = -(price return) - costs; borrow fees not modelled (noted).
# Value here = price-based value proxies (cheap vs own history), since
# historical accounting fundamentals are not available to backtest.
#
# Usage:  python variants4.py

import json
import math
import time

from backtest import features, stats, TOP_N, COST, NONSTOCK
from variants import load_data

HOLD = 5
LOOKBACK = 70
LONG_LB = 260


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
    print(f"{len(rebalances)} weekly rebalances (bar {LONG_LB} onward, ~4y sample).")

    # ---- candidates ----
    def downtrend(f):
        return [(s, x) for s, x in f.items()
                if x["px"] < x["sma20"] < x["sma50"]]

    SHORTS = {
        "short weak momentum": (lambda f: [s for s, x in sorted(downtrend(f),
            key=lambda p: p[1]["ret60"] + p[1]["ret20"])[:TOP_N]], False),
        "short weak mom + bear regime": (lambda f: [s for s, x in sorted(downtrend(f),
            key=lambda p: p[1]["ret60"] + p[1]["ret20"])[:TOP_N]], True),
        "short rally in downtrend": (lambda f: [s for s, x in sorted(
            [(s, x) for s, x in f.items()
             if x["px"] < x["sma50"] and x["sma20"] < x["sma50"]
             and x["rsi3"] is not None and x["rsi3"] > 70],
            key=lambda p: -p[1]["rsi3"])[:TOP_N]], False),
        "short rally + bear regime": (lambda f: [s for s, x in sorted(
            [(s, x) for s, x in f.items()
             if x["px"] < x["sma50"] and x["sma20"] < x["sma50"]
             and x["rsi3"] is not None and x["rsi3"] > 70],
            key=lambda p: -p[1]["rsi3"])[:TOP_N]], True),
        "short 5d spike + bear regime": (lambda f: [s for s, x in sorted(
            [(s, x) for s, x in f.items() if x["px"] < x["sma50"]],
            key=lambda p: -p[1]["ret5"])[:TOP_N]], True),
    }

    VALUE = {
        "deep 52w drawdown": lambda f: [s for s, x in sorted(
            [(s, x) for s, x in f.items() if x.get("dd52") is not None],
            key=lambda p: p[1]["dd52"])[:TOP_N]],
        "deep drawdown + turning up": lambda f: [s for s, x in sorted(
            [(s, x) for s, x in f.items()
             if x.get("dd52") is not None and x["px"] > x["sma20"]],
            key=lambda p: p[1]["dd52"])[:TOP_N]],
        "12-month losers": lambda f: [s for s, x in sorted(
            [(s, x) for s, x in f.items() if x.get("ret252") is not None],
            key=lambda p: p[1]["ret252"])[:TOP_N]],
        "12m losers + turning up": lambda f: [s for s, x in sorted(
            [(s, x) for s, x in f.items()
             if x.get("ret252") is not None and x["px"] > x["sma20"]],
            key=lambda p: p[1]["ret252"])[:TOP_N]],
        "below 200d MA + turning up": lambda f: [s for s, x in sorted(
            [(s, x) for s, x in f.items()
             if x.get("sma200") and x["px"] < x["sma200"] and x["px"] > x["sma20"]],
            key=lambda p: p[1]["px"] / p[1]["sma200"])[:TOP_N]],
    }

    weekly = {k: [] for k in list(SHORTS) + list(VALUE)}
    spy_weekly = []

    for t in rebalances:
        feats = {}
        for sym, (cl, hi, vo, first) in aligned.items():
            if sym in NONSTOCK or first > t - LOOKBACK:
                continue
            f = features(cl[t - LOOKBACK + 1: t + 1],
                         hi[t - LOOKBACK + 1: t + 1],
                         vo[t - LOOKBACK + 1: t + 1])
            if first <= t - LONG_LB:
                wl = cl[t - LONG_LB + 1: t + 1]
                f["sma200"] = sum(wl[-200:]) / 200
                f["ret252"] = f["px"] / wl[0] - 1
                f["dd52"] = f["px"] / max(hi[t - LONG_LB + 1: t + 1]) - 1
            feats[sym] = f
        spy_win = spy_closes[t - LOOKBACK + 1: t + 1]
        spyf = features(spy_win, spy_win, [0] * LOOKBACK)
        bear = spyf["px"] < spyf["sma50"]
        spy_weekly.append(spy_closes[t + HOLD] / spy_closes[t] - 1)

        for key, (fn, gated) in SHORTS.items():
            if gated and not bear:
                weekly[key].append(0.0)
                continue
            picks = fn(feats)
            if picks:
                rets = [-(aligned[s][0][t + HOLD] / aligned[s][0][t] - 1) - COST
                        for s in picks]
                weekly[key].append(sum(rets) / len(rets))
            else:
                weekly[key].append(0.0)

        for key, fn in VALUE.items():
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
          f"{'total':>8}{'H1':>8}{'H2':>8}")
    results = {}
    for key in weekly:
        st = stats(weekly[key], spy_weekly)
        results[key] = st
        tag = "S" if key in SHORTS else "V"
        print(f"[{tag}] {key:<28}{st['hitRate']:>6}{st['avgWeek']:>8}{st['sharpe']:>8}"
              f"{st['maxDD']:>8}{st['total']:>8}{st['avgWeekH1']:>8}{st['avgWeekH2']:>8}")

    with open("variants_results.json", encoding="utf-8") as f:
        merged = json.load(f)
    merged["variantsExtra"] = results
    dates = [time.strftime("%Y-%m-%d", time.gmtime(cal[t] * 86400)) for t in rebalances]
    merged["curvesExtra"] = {"dates": dates}
    for key in weekly:
        merged["curvesExtra"][key] = [round(w, 5) for w in weekly[key]]
    with open("variants_results.json", "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=1)
    print("\nMerged into variants_results.json (variantsExtra + curvesExtra)")
    print("NOTE: short returns exclude borrow fees; value = price-proxy value "
          "(no historical fundamentals available).")


if __name__ == "__main__":
    main()
