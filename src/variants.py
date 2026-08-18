# Round 2: refined strategy variants over the same 5y weekly framework.
# Adds: SPY regime filter (cash when SPY < SMA50), skip-recent-week momentum,
# risk-adjusted momentum, and top-N concentration tests.
# Caches raw 10y data in state/px10y_cache.json so re-runs are instant (refetched when
# >24h old, when the universe has gained names, or with --refetch).
#
# Shared by ALL rounds (variants2-5.py import from here):
#   load_data()  - cache + point-in-time mask (S&P names eligible only from their index
#                  add date; --nopit / BT_PIT=0 disables)
#   align(data)  - calendar-aligned (closes, highs, vols, first, opens) per symbol
#   hold_ret()   - one-hold return under the ENTRY convention:
#                  "nextopen" (default): buy next session's open, sell the open HOLD
#                  sessions later - what a trader acting on the after-close picks gets;
#                  "close" (--closeentry / BT_ENTRY=close): the old same-close fill.
#   BT_RESULTS env var overrides the results file (for side-by-side comparison runs).
#
# Usage:  python variants.py [--refetch] [--closeentry] [--nopit]

import json
import math
import os
import sys
import time
import concurrent.futures

from backtest import fetch5y, features, stats, UNIVERSE, TOP_N, HOLD, LOOKBACK, COST, NONSTOCK
from screener import UNIVERSE_INFO

# Repo layout: scripts live in src/, mutable state (caches, results) in state/.
# VR is imported by variants2-5.py so all rounds share the same results file.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state")
VR = os.environ.get("BT_RESULTS") or os.path.join(STATE, "variants_results.json")
CACHE = os.path.join(STATE, "px10y_cache.json")
# Sidecar recording which symbols the cache ATTEMPTED (not which succeeded), so names that
# legitimately return nothing (delisted, non-NYSE/Nasdaq) don't force a refetch every run.
CACHE_META = os.path.join(STATE, "px10y_cache.meta.json")

ENTRY = "close" if ("--closeentry" in sys.argv or os.environ.get("BT_ENTRY") == "close") \
    else "nextopen"
PIT = not ("--nopit" in sys.argv or os.environ.get("BT_PIT") == "0")
_EPOCH = 719163  # datetime.date(1970, 1, 1).toordinal()


def _mask_pit(data):
    """Drop each S&P name's bars before its index add date, so a backtest only sees a
    name once it was actually a constituent (partial survivorship fix: additions only;
    deletions can't be recovered from the current list). Nasdaq-100-only names and
    pinned extras have no add date and keep full history."""
    import datetime
    n = 0
    for sym, series in data.items():
        added = UNIVERSE_INFO.get(sym.replace("-", "."), {}).get("added")
        if not added:
            continue
        try:
            cut = datetime.date.fromisoformat(added).toordinal() - _EPOCH
        except ValueError:
            continue
        if cut > min(series, default=cut):
            for d in [d for d in series if d < cut]:
                del series[d]
            n += 1
    if n:
        print(f"Point-in-time: masked pre-add-date history for {n} S&P names.")
    return data


def _finish(data):
    return _mask_pit(data) if PIT else data


def load_data(force=None):
    if force is None:
        force = "--refetch" in sys.argv
    syms = UNIVERSE + sorted(NONSTOCK)
    if not force and os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < 86400:
        try:
            with open(CACHE_META, encoding="utf-8") as f:
                attempted = set(json.load(f).get("universe", []))
        except Exception:
            attempted = set()
        missing = sorted(set(syms) - attempted)
        if not missing:
            with open(CACHE, encoding="utf-8") as f:
                raw = json.load(f)
            probe = next((v for d in raw.values() for v in d.values()), None)
            if probe is not None and len(probe) >= 4:
                return _finish({s: {int(k): tuple(v) for k, v in d.items()}
                                for s, d in raw.items()})
            print("px10y cache predates open prices; refetching all.")
        else:
            print(f"px10y cache lacks {len(missing)} universe names "
                  f"({', '.join(missing[:8])}{'...' if len(missing) > 8 else ''}); refetching all.")
    print(f"Fetching 10y history for {len(syms)} symbols...")
    data = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(fetch5y, syms):
            if res:
                data[res[0]] = res[1]
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(data, f)
    with open(CACHE_META, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), "universe": sorted(syms)}, f)
    return _finish(data)


def align(data):
    """-> (cal, aligned) where aligned[sym] = (closes, highs, vols, first, opens), each
    list on SPY's calendar and forward-filled after the first observation."""
    cal = sorted(data["SPY"].keys())
    aligned = {}
    for sym, series in data.items():
        closes = [None] * len(cal)
        highs = [None] * len(cal)
        vols = [None] * len(cal)
        opens = [None] * len(cal)
        last = None
        for i, d in enumerate(cal):
            if d in series:
                last = series[d]
            if last:
                closes[i], highs[i], vols[i] = last[:3]
                opens[i] = last[3] if len(last) > 3 and last[3] else last[0]
        first = next((i for i, c in enumerate(closes) if c is not None), None)
        if first is not None:
            aligned[sym] = (closes, highs, vols, first, opens)
    return cal, aligned


def hold_ret(aligned, sym, t, hold):
    """Gross return of holding `sym` for one rebalance period signalled at close t.
    nextopen: open[t+1] -> open[t+1+hold] (falls back to close[t+hold] at the end of the
    sample); close: close[t] -> close[t+hold]."""
    cl, _, _, _, op = aligned[sym]
    if ENTRY == "close":
        return cl[t + hold] / cl[t] - 1
    entry = op[t + 1] if t + 1 < len(op) and op[t + 1] else cl[t]
    j = t + 1 + hold
    exit_ = op[j] if j < len(op) and op[j] else cl[t + hold]
    return exit_ / entry - 1


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
    print(f"Universe: {len(data) - 1} names + SPY (cached). Entry: {ENTRY}; "
          f"point-in-time: {'on' if PIT else 'off'}.")

    cal, aligned = align(data)
    spy_closes = aligned["SPY"][0]
    rebalances = list(range(LOOKBACK, len(cal) - HOLD, HOLD))

    weekly = {k: [] for k in VARIANTS}
    exposure = {k: 0 for k in VARIANTS}
    spy_weekly = []

    for t in rebalances:
        feats = {}
        for sym, (cl, hi, vo, first, _op) in aligned.items():
            if sym in NONSTOCK or first > t - LOOKBACK:
                continue
            feats[sym] = features(cl[t - LOOKBACK + 1: t + 1],
                                  hi[t - LOOKBACK + 1: t + 1],
                                  vo[t - LOOKBACK + 1: t + 1])
        spy_win = spy_closes[t - LOOKBACK + 1: t + 1]
        spyf = features(spy_win, spy_win, [0] * LOOKBACK)
        spy_regime_on = spyf["px"] > spyf["sma50"]
        spy_weekly.append(hold_ret(aligned, "SPY", t, HOLD))

        for key, (fn, regime) in VARIANTS.items():
            if regime and not spy_regime_on:
                weekly[key].append(0.0)  # cash
                continue
            picks = fn(feats, spyf)
            if picks:
                rets = [hold_ret(aligned, s, t, HOLD) - COST for s in picks]
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
    if os.path.exists(VR):
        try:
            with open(VR, encoding="utf-8") as f:
                merged = json.load(f)
        except Exception:
            pass
    merged["spy"] = spy_stats
    merged["variants"] = results
    merged["curves"] = curves
    merged["meta"] = {"entry": ENTRY, "pointInTime": PIT, "universe": len(data) - 1,
                      "cost": COST, "run": time.strftime("%Y-%m-%d %H:%M")}
    with open(VR, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=1)
    print("\nWrote variants_results.json (merged)")


if __name__ == "__main__":
    main()
