# Is the kill switch comparing like with like? (No - and this measures by how much.)
#
# strategy_health() in screener.py judges the LIVE paper log against the BACKTEST
# expectation in variants_results.json. But the two are built on different rules:
#
#                          entry              exit                    stops/targets
#   live paper log         close[t]           close[t+hold]           1.5x/2x ATR, stop
#                                                                     checked before
#                                                                     target on a bar
#   variants*.py backtest  open[t+1]          open[t+1+hold]          none
#
# So a strategy can read "demoted" purely because the 1.5x ATR stop knocked it out of
# trades the backtest simply held. 614 of the paper log's 2,326 closed trades were
# stopped out, so this is not a rounding difference.
#
# This script runs the same pickers over the same 10y data under all four combinations
# of (entry convention) x (stops on/off), so the gap can be attributed instead of
# guessed. The "close + stops" column is the apples-to-apples backtest expectation for
# the live paper log - that is the number strategy_health() should be using.
#
# Returns are GROSS (no cost), per TRADE not per rebalance, to match how picks_log.json
# reports retPct. variants*.py subtracts 0.10% round trip; that is not applied here.
#
# Usage:  python src/stop_test.py [--refetch] [--nopit]

import json
import os
import time

from backtest import features, HOLD, NONSTOCK, LOOKBACK
from variants import load_data, align, align_lows, PIT
from delay_test import build_strategies, LONG_LB, HOLD14

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state")
OUT = os.path.join(STATE, "stop_test.json")

# Live ATR multiples, from the dashboard strategy cards (data.js stopMult/targetMult).
MULTS = {5: (1.5, 2.0), 10: (2.0, 3.0)}
ATR_N = 14


def atr_at(highs, lows, closes, t, n=ATR_N, window=LOOKBACK):
    """Wilder ATR over the `window` bars ending at t. screener.py seeds on the first n
    true ranges and smooths through its whole 2y series; Wilder converges, so a 70-bar
    window matches it closely."""
    lo_i = max(1, t - window + 1)
    trs = []
    for i in range(lo_i, t + 1):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        if h is None or l is None or pc is None:
            return None
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < n + 1:
        return None
    a = sum(trs[:n]) / n
    for i in range(n, len(trs)):
        a = (a * (n - 1) + trs[i]) / n
    return a


def simulate(closes, highs, lows, opens, t, hold, a, entry_mode, use_stops):
    """One trade. Returns (gross return, exit type) or None.

    Mirrors update_paper_log(): scan bars t+1 .. t+hold, stop checked BEFORE target on
    the same bar (conservative - a bar that touches both is booked as a loss)."""
    if entry_mode == "close":
        entry = closes[t]
        last = t + hold
        time_exit = closes[last] if last < len(closes) else None
    else:                                    # nextopen
        i = t + 1
        if i >= len(opens):
            return None
        entry = opens[i] or closes[i]
        last = t + hold                      # same bars scanned; exit at the open after
        j = t + 1 + hold
        time_exit = (opens[j] or closes[j]) if j < len(opens) else None
    if not entry or not time_exit or last >= len(closes):
        return None

    if use_stops and a:
        stop = entry - MULTS[hold][0] * a
        target = entry + MULTS[hold][1] * a
        for k in range(t + 1, last + 1):
            lo, hi = lows[k], highs[k]
            if lo is not None and lo <= stop:
                return stop / entry - 1, "stopped"
            if hi is not None and hi >= target:
                return target / entry - 1, "target"
    return time_exit / entry - 1, "time-exit"


def main():
    from screener import SECTOR_OF
    data = load_data()
    cal, aligned = align(data)
    lows_by_sym = align_lows(data, cal)
    if len(lows_by_sym) < len(aligned) * 0.9:
        raise SystemExit("px10y cache has lows for only %d of %d names - "
                         "run with --refetch first." % (len(lows_by_sym), len(aligned)))
    spy_closes = aligned["SPY"][0]
    strategies = build_strategies(SECTOR_OF)

    tail = HOLD14 + 2
    rebalances = list(range(LONG_LB, len(cal) - tail, HOLD))
    print("Universe %d names + SPY | point-in-time=%s | %d weekly rebalances, %s -> %s"
          % (len(data) - 1, "on" if PIT else "off", len(rebalances),
             time.strftime("%Y-%m-%d", time.gmtime(cal[rebalances[0]] * 86400)),
             time.strftime("%Y-%m-%d", time.gmtime(cal[rebalances[-1]] * 86400))))
    print("Gross returns per trade (no cost), stops %s/%s x ATR(%d) for 5d holds, "
          "%s/%s for 10d.\n" % (MULTS[5][0], MULTS[5][1], ATR_N, MULTS[10][0], MULTS[10][1]))

    combos = [("close", True), ("close", False), ("nextopen", True), ("nextopen", False)]
    rets = {k: {c: [] for c in combos} for k in strategies}
    mix = {k: {"stopped": 0, "target": 0, "time-exit": 0} for k in strategies}

    for t in rebalances:
        feats = {}
        for sym, (cl, hi, vo, first, _op) in aligned.items():
            if sym in NONSTOCK or first > t - LOOKBACK:
                continue
            f = features(cl[t - LOOKBACK + 1: t + 1],
                         hi[t - LOOKBACK + 1: t + 1],
                         vo[t - LOOKBACK + 1: t + 1])
            if first <= t - LONG_LB:
                wl = cl[t - LONG_LB + 1: t + 1]
                f["sma200"] = sum(wl[-200:]) / 200
                f["dd52"] = f["px"] / max(hi[t - LONG_LB + 1: t + 1]) - 1
            feats[sym] = f

        spy_win = spy_closes[t - LOOKBACK + 1: t + 1]
        spyf = features(spy_win, spy_win, [0] * LOOKBACK)
        regime_on = spyf["px"] > spyf["sma50"]

        for key, (fn, gated, hold) in strategies.items():
            if gated and not regime_on:
                continue                      # cash: no trade to measure
            for sym in fn(feats):
                cl, hi, _vo, _first, op = aligned[sym]
                lo = lows_by_sym.get(sym)
                if not lo:
                    continue
                a = atr_at(hi, lo, cl, t)
                for combo in combos:
                    r = simulate(cl, hi, lo, op, t, hold, a, combo[0], combo[1])
                    if r is None:
                        continue
                    rets[key][combo].append(r[0])
                    if combo == ("close", True):
                        mix[key][r[1]] += 1

    # ---- report ----------------------------------------------------------------------
    live = load_live_stats()
    print("%-15s%10s%10s%10s%10s%8s%10s%10s"
          % ("strategy", "close+SL", "close", "nextop+SL", "nextopen", "n", "LIVE", "gap"))
    print("%-15s%10s%10s%10s%10s%8s%10s%10s"
          % ("", "(=live)", "", "", "(=backtest)", "", "paper", "live-model"))
    results = {}
    for key in strategies:
        row = {}
        for combo in combos:
            v = rets[key][combo]
            row["%s%s" % (combo[0], "+stops" if combo[1] else "")] = \
                round(sum(v) / len(v) * 100, 3) if v else None
        n = len(rets[key][("close", True)])
        model = row["close+stops"]
        lv = live.get(key)
        gap = round(lv - model, 3) if (lv is not None and model is not None) else None
        print("%-15s%10s%10s%10s%10s%8d%10s%10s"
              % (key, row["close+stops"], row["close"], row["nextopen+stops"],
                 row["nextopen"], n,
                 "-" if lv is None else "%.3f" % lv,
                 "-" if gap is None else "%+.3f" % gap))
        row["n"] = n
        row["livePaperAvgPct"] = lv
        row["liveMinusModel"] = gap
        row["exitMix"] = dict(mix[key])
        results[key] = row

    print("\n--- EXIT MIX under the live convention (close + stops) ---")
    print("%-15s%10s%10s%12s   compare with picks_log.json" % ("strategy", "stopped%", "target%", "time-exit%"))
    for key in strategies:
        m = mix[key]
        tot = sum(m.values())
        if not tot:
            continue
        print("%-15s%10.1f%10.1f%12.1f"
              % (key, m["stopped"] / tot * 100, m["target"] / tot * 100,
                 m["time-exit"] / tot * 100))

    payload = {"meta": {"run": time.strftime("%Y-%m-%d %H:%M"), "weeks": len(rebalances),
                        "gross": True, "atrN": ATR_N, "mults": {str(k): v for k, v in MULTS.items()},
                        "pointInTime": PIT, "note": "close+stops is the apples-to-apples "
                        "expectation for picks_log.json; nextopen (no stops) is what "
                        "variants_results.json reports"},
               "strategies": results}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    print("\nWrote %s" % OUT)


def load_live_stats():
    """Realised avg % per closed trade from the paper log, per strategy."""
    path = os.path.join(STATE, "picks_log.json")
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)["entries"]
    except Exception:
        return {}
    agg = {}
    for e in entries:
        if e.get("status") == "open" or e.get("retPct") is None:
            continue
        a = agg.setdefault(e["strategy"], [])
        a.append(e["retPct"])
    return {k: round(sum(v) / len(v), 3) for k, v in agg.items() if v}


if __name__ == "__main__":
    main()
