# Does acting late cost anything?
#
# The live picks are signalled at a US close (04:00 SGT). The backtest already assumes
# you buy the NEXT session's open (21:30 SGT the following evening) - a ~17.5h wait.
# This script asks the question behind "am I late to the game": if you wait even longer,
# how fast does each strategy's edge decay?
#
#   delay 0 = buy open[t+1]   <- what variants*.py assumes, and what a resting limit
#                                order placed during SGT daytime actually gets
#   delay 1 = buy open[t+2]   <- you missed a day
#   delay 2 = buy open[t+3]
#   delay 3 = buy open[t+4]
#
# Two exit conventions, because they answer different questions:
#   shift  - hold the same NUMBER of days from the later entry ("is the signal still
#            good N days on?")
#   fixed  - keep the original sell-by date, so a later entry just means a shorter hold
#            (what happens in practice if you stick to the weekly rotation calendar)
#
# It also decomposes the delay-0 return into the overnight gap you can never trade
# (close[t] -> open[t+1]) versus everything after it (open[t+1] -> exit). If a
# strategy's edge lives entirely in that gap, it is untradeable from Singapore no
# matter how disciplined you are.
#
# Reuses the production data path (variants.load_data / align, backtest.features) so the
# numbers are directly comparable to variants_results.json. Writes state/delay_test.json.
# Does NOT touch variants_results.json.
#
# Usage:  python src/delay_test.py [--maxdelay 3] [--refetch] [--nopit]

import json
import math
import os
import sys
import time

from backtest import features, stats, TOP_N, HOLD, COST, NONSTOCK, LOOKBACK
from variants import load_data, align, VR, ENTRY, PIT

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state")
OUT = os.path.join(STATE, "delay_test.json")

LONG_LB = 260          # bars needed for dd52 / sma200
HOLD14 = 10            # the 14-day-hold family (mom14, sector14)


def _arg(flag, default):
    return int(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default


MAX_DELAY = _arg("--maxdelay", 3)


# ---- return conventions -------------------------------------------------------------

def ret_delayed(aligned, sym, t, hold, delay, fixed_exit):
    """Signal at close t; buy the open `delay` sessions after the normal next open.
    fixed_exit=False -> sell `hold` sessions after entry (same holding period).
    fixed_exit=True  -> sell at the original open[t+1+hold] (shorter hold)."""
    cl, _, _, _, op = aligned[sym]
    i = t + 1 + delay
    j = (t + 1 + hold) if fixed_exit else (i + hold)
    if i >= len(op) or j >= len(op) or j <= i:
        return None
    entry = op[i] or cl[i]
    exit_ = op[j] or cl[j]
    if not entry or not exit_:
        return None
    return exit_ / entry - 1


def ret_parts(aligned, sym, t, hold):
    """delay-0 return split into the untradeable overnight gap and the rest."""
    cl, _, _, _, op = aligned[sym]
    if t + 1 + hold >= len(op):
        return None
    c0 = cl[t]
    o1 = op[t + 1] or cl[t + 1]
    oj = op[t + 1 + hold] or cl[t + 1 + hold]
    if not (c0 and o1 and oj):
        return None
    return {"gap": o1 / c0 - 1, "rest": oj / o1 - 1, "full": oj / c0 - 1}


# ---- strategy pickers: mirrors of the live dashboard cards ---------------------------
# (screener.py strategy_picks(); regime gating matches the "+ regime" labels)

def _uptrend(f):
    return [(s, x) for s, x in f.items()
            if x["px"] > x["sma20"] > x["sma50"] and (x["rsi14"] or 0) <= 75]


def _mom_ranked(f):
    c = _uptrend(f)
    c.sort(key=lambda p: -(p[1]["ret60"] + p[1]["ret20"]))
    return c


def _sector_best(ranked, sector_of):
    # aligned keys are Yahoo-form (BRK-B); SECTOR_OF is keyed dot-form (BRK.B)
    best = {}
    for s, _x in ranked:
        best.setdefault(sector_of.get(s.replace("-", "."), "Other"), s)
    return list(best.values())


def _zblend(f):
    pool = [(s, x) for s, x in f.items() if x["px"] > x["sma50"]]
    if len(pool) < 2:
        return []

    def z(vals):
        m = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals)) or 1e-9
        return [(v - m) / sd for v in vals]

    zm = z([x["ret60"] + x["ret20"] for _s, x in pool])
    zv = z([x["vol20"] for _s, x in pool])
    order = sorted(zip([a - b for a, b in zip(zm, zv)], [s for s, _x in pool]),
                   key=lambda p: -p[0])
    return [s for _sc, s in order[:TOP_N]]


def build_strategies(sector_of):
    """key -> (picker(feats) -> [syms], regime_gated, hold)"""
    return {
        "momTop10": (lambda f: [s for s, _x in _mom_ranked(f)[:10]], True, HOLD),
        "momTop5": (lambda f: [s for s, _x in _mom_ranked(f)[:TOP_N]], False, HOLD),
        "lowvol": (lambda f: [s for s, _x in sorted(
            [(s, x) for s, x in f.items()
             if x["px"] > x["sma20"] and x["sma20"] > x["sma50"]],
            key=lambda p: p[1]["vol20"])[:TOP_N]], True, HOLD),
        "sectorNeutral": (lambda f: _sector_best(_mom_ranked(f), sector_of), True, HOLD),
        "weeklyDip": (lambda f: [s for s, _x in sorted(
            [(s, x) for s, x in f.items()
             if x["px"] > x["sma50"] and x["sma20"] > x["sma50"]],
            key=lambda p: p[1]["ret5"])[:TOP_N]], False, HOLD),
        "valueDD": (lambda f: [s for s, _x in sorted(
            [(s, x) for s, x in f.items() if x.get("dd52") is not None],
            key=lambda p: p[1]["dd52"])[:TOP_N]], False, HOLD),
        "value200": (lambda f: [s for s, _x in sorted(
            [(s, x) for s, x in f.items()
             if x.get("sma200") and x["px"] < x["sma200"] and x["px"] > x["sma20"]],
            key=lambda p: p[1]["px"] / p[1]["sma200"])[:TOP_N]], False, HOLD),
        "zblend": (_zblend, True, HOLD),
        "mom14": (lambda f: [s for s, _x in _mom_ranked(f)[:TOP_N]], False, HOLD14),
        "sector14": (lambda f: _sector_best(_mom_ranked(f), sector_of), True, HOLD14),
    }


def main():
    from screener import SECTOR_OF
    data = load_data()
    cal, aligned = align(data)
    spy_closes = aligned["SPY"][0]
    strategies = build_strategies(SECTOR_OF)

    # Leave room for the longest hold at the deepest delay so every strategy/delay
    # combination is measured over exactly the same set of rebalance dates.
    tail = HOLD14 + MAX_DELAY + 2
    rebalances = list(range(LONG_LB, len(cal) - tail, HOLD))
    span = (time.strftime("%Y-%m-%d", time.gmtime(cal[rebalances[0]] * 86400)),
            time.strftime("%Y-%m-%d", time.gmtime(cal[rebalances[-1]] * 86400)))
    print("Universe %d names + SPY | entry=%s point-in-time=%s"
          % (len(data) - 1, ENTRY, "on" if PIT else "off"))
    print("%d weekly rebalances, %s -> %s" % (len(rebalances), span[0], span[1]))
    print("Delays 0..%d sessions, cost %.2f%% round trip per position.\n"
          % (MAX_DELAY, COST * 100))

    delays = list(range(MAX_DELAY + 1))
    weekly = {}
    for k in strategies:
        weekly[k] = {}
        for mode in ("shift", "fixed"):
            for d in delays:
                weekly[k][(mode, d)] = []
    parts = {k: {"gap": [], "rest": [], "full": []} for k in strategies}
    spy_weekly = []

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
        spy_weekly.append(ret_delayed(aligned, "SPY", t, HOLD, 0, False) or 0.0)

        for key, (fn, gated, hold) in strategies.items():
            picks = [] if (gated and not regime_on) else fn(feats)
            if not picks:
                for mode in ("shift", "fixed"):
                    for d in delays:
                        weekly[key][(mode, d)].append(0.0)   # cash
                continue
            for mode, fixed in (("shift", False), ("fixed", True)):
                for d in delays:
                    rs = []
                    for s in picks:
                        r = ret_delayed(aligned, s, t, hold, d, fixed)
                        if r is not None:
                            rs.append(r - COST)
                    weekly[key][(mode, d)].append(sum(rs) / len(rs) if rs else 0.0)
            for s in picks:
                p = ret_parts(aligned, s, t, hold)
                if p:
                    for kk in parts[key]:
                        parts[key][kk].append(p[kk])

    # ---- report ----------------------------------------------------------------------
    spy_st = stats(spy_weekly, spy_weekly)
    print("SPY over the same weeks: avg %s%%/wk, sharpe %s, total %s%%\n"
          % (spy_st["avgWeek"], spy_st["sharpe"], spy_st["total"]))

    results = {}
    for mode, title in (("shift", "SAME HOLDING PERIOD, STARTED LATER"),
                        ("fixed", "SAME SELL-BY DATE, SO A LATER ENTRY = SHORTER HOLD")):
        print("--- %s ---" % title)
        head = "".join("%9s" % ("d%d" % d) for d in delays)
        print("%-15s%s%9s   avg %% per rebalance" % ("strategy", head, "decay"))
        for key in strategies:
            row = [stats(weekly[key][(mode, d)], spy_weekly) for d in delays]
            avgs = [r["avgWeek"] for r in row]
            print("%-15s%s%9.3f" % (key, "".join("%9.3f" % a for a in avgs),
                                    avgs[-1] - avgs[0]))
            results.setdefault(key, {})[mode] = {str(d): row[i]
                                                 for i, d in enumerate(delays)}
        print()

    print("--- WHERE THE DELAY-0 RETURN COMES FROM (per position, gross) ---")
    print("%-15s%9s%9s%9s%11s   gap = close[t] -> open[t+1], untradeable"
          % ("strategy", "gap %", "rest %", "full %", "gap share"))
    for key in strategies:
        p = parts[key]
        if not p["full"]:
            continue
        g = sum(p["gap"]) / len(p["gap"]) * 100
        r = sum(p["rest"]) / len(p["rest"]) * 100
        fu = sum(p["full"]) / len(p["full"]) * 100
        share = (g / fu * 100) if abs(fu) > 1e-9 else float("nan")
        print("%-15s%9.3f%9.3f%9.3f%10.0f%%" % (key, g, r, fu, share))
        results.setdefault(key, {})["parts"] = {
            "gapPct": round(g, 4), "restPct": round(r, 4), "fullPct": round(fu, 4),
            "gapSharePct": None if math.isnan(share) else round(share, 1),
            "n": len(p["full"])}

    payload = {"meta": {"run": time.strftime("%Y-%m-%d %H:%M"), "weeks": len(rebalances),
                        "from": span[0], "to": span[1], "maxDelay": MAX_DELAY,
                        "cost": COST, "entry": ENTRY, "pointInTime": PIT,
                        "hold": HOLD, "hold14": HOLD14,
                        "comparableTo": os.path.basename(VR)},
               "spy": spy_st, "strategies": results}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    print("\nWrote %s" % OUT)


if __name__ == "__main__":
    main()
