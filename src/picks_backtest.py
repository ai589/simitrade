# 10-year backtest of the PRICE-SETUP part of Top Buys / Top Sells.
#
# What this can and cannot tell you:
#   - It replays picks.shortlist() + picks.rank() every week over the 10y cache, with no
#     news and no macro input, and holds the top names for 5 trading days.
#   - The news and macro overlays are NOT in here. There are no historical headlines to
#     replay, so their value can only be judged on the live paper log.
#   - Earnings dates are not in the cache either, so the live earnings exclusion is not
#     applied. Expect the live list to be slightly calmer than this.
#
# Conventions match variants*.py so the numbers sit alongside the other strategy cards:
# weekly rebalance, enter next open, exit at the open 5 sessions later, 0.10% round-trip
# cost, point-in-time S&P membership, equal weight, cash in weeks with no qualifying pick.
#
# Usage:  python src/picks_backtest.py [--refetch] [--nopit]
# Writes: state/picks_backtest.json  (read by screener.lead_backtest_stats())

import json
import math
import os
import time

import picks
from backtest import rsi, stats, COST, HOLD, NONSTOCK
from variants import load_data, align, align_lows, hold_ret, ENTRY, PIT

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "state", "picks_backtest.json")

LONG_LB = 260                 # bars needed for the 200-day average and 52-week high
MIN_DOLLAR_VOL = 50_000_000   # same liquidity floor as screener.analyse()


def atr14(cl, hi, lo):
    """Wilder ATR over the window, in price units. Without lows (old cache rows) the
    range is approximated from highs and closes only."""
    trs = []
    for i in range(1, len(cl)):
        pc = cl[i - 1]
        if lo is not None and lo[i] is not None:
            trs.append(max(hi[i] - lo[i], abs(hi[i] - pc), abs(lo[i] - pc)))
        else:
            trs.append(max(abs(hi[i] - pc), abs(cl[i] - pc)) * 1.5)
    a = sum(trs[:14]) / 14
    for x in trs[14:]:
        a = (a * 13 + x) / 14
    return a


def make_row(sym, cl, hi, vo, lo, sector):
    """A row with the fields picks.py reads, built from LONG_LB bars ending at t.
    Mirrors screener.analyse(); returns None when the name is too illiquid."""
    px = cl[-1]
    dvol = sum(c * v for c, v in zip(cl[-20:], vo[-20:])) / 20
    if dvol < MIN_DOLLAR_VOL:
        return None
    sma20, sma50, sma200 = sum(cl[-20:]) / 20, sum(cl[-50:]) / 50, sum(cl[-200:]) / 200
    rets = [cl[i] / cl[i - 1] - 1 for i in range(len(cl) - 20, len(cl))]
    m = sum(rets) / 20
    vol20 = math.sqrt(sum((x - m) ** 2 for x in rets) / 20) * math.sqrt(252) * 100
    a = atr14(cl[-40:], hi[-40:], lo[-40:] if lo else None)
    return {
        "sym": sym, "name": sym, "sector": sector, "px": px, "entry": px, "atr": a,
        "atrPct": a / px * 100, "vol20": vol20,
        "ret5": (px / cl[-6] - 1) * 100, "ret20": (px / cl[-21] - 1) * 100,
        "ret60": (px / cl[-61] - 1) * 100,
        "rsi": rsi(cl[-60:], 14), "rsi3": rsi(cl[-10:], 3),
        "dd52": (px / max(hi[-252:]) - 1) * 100, "px200": px / sma200 * 100,
        "volRatio": (sum(vo[-5:]) / 5) / max(1e-9, sum(vo[-20:]) / 20),
        "distHi20": (px / max(hi[-20:]) - 1) * 100,
        "aboveSma20": px > sma20, "aboveSma50": px > sma50, "trendUp": sma20 > sma50,
        "earnSoon": False,
    }


def main():
    from screener import SECTOR_OF
    data = load_data()
    cal, aligned = align(data)
    lows = align_lows(data, cal)
    rebalances = list(range(LONG_LB, len(cal) - HOLD - 2, HOLD))
    day = lambda i: time.strftime("%Y-%m-%d", time.gmtime(cal[i] * 86400))
    print("Universe %d names | entry=%s point-in-time=%s | %d weekly rebalances %s -> %s"
          % (len(data) - 1, ENTRY, "on" if PIT else "off", len(rebalances),
             day(rebalances[0]), day(rebalances[-1])))

    weekly = {"topBuys": [], "topSells": []}
    counts = {"topBuys": [], "topSells": []}
    by_setup = {}
    spy_weekly = []
    for n, t in enumerate(rebalances):
        rows = []
        for sym, (cl, hi, vo, first, _op) in aligned.items():
            if sym in NONSTOCK or first > t - LONG_LB:
                continue
            s = slice(t - LONG_LB + 1, t + 1)
            lo = lows.get(sym)
            r = make_row(sym, cl[s], hi[s], vo[s], lo[s] if lo else None,
                         SECTOR_OF.get(sym.replace("-", "."), "Other"))
            if r:
                rows.append(r)
        buys, sells = picks.shortlist(rows)
        for key, cands, sign in (("topBuys", buys, 1), ("topSells", sells, -1)):
            top, _ = picks.rank(cands, None, None, {})
            rets = []
            for p in top:
                g = sign * hold_ret(aligned, p["sym"], t, HOLD)
                rets.append(g - COST)
                b = by_setup.setdefault(p["setup"], [])
                b.append(g)
            weekly[key].append(sum(rets) / len(rets) if rets else 0.0)
            counts[key].append(len(rets))
        spy_weekly.append(hold_ret(aligned, "SPY", t, HOLD))
        if n % 50 == 0:
            print("  %s  %d/%d" % (day(t), n, len(rebalances)))

    out_stats = {}
    for key in weekly:
        st = stats(weekly[key], spy_weekly)
        st["avgNames"] = round(sum(counts[key]) / len(counts[key]), 1)
        st["emptyWeeks"] = sum(1 for c in counts[key] if c == 0)
        out_stats[key] = st
    setups = {k: {"trades": len(v), "avgPct": round(sum(v) / len(v) * 100, 3),
                  "hitRate": round(sum(1 for x in v if x > 0) / len(v) * 100, 1)}
              for k, v in sorted(by_setup.items())}
    out = {"meta": {"run": time.strftime("%Y-%m-%d %H:%M"), "from": day(rebalances[0]),
                    "to": day(rebalances[-1]), "entry": ENTRY, "pointInTime": PIT,
                    "costPct": COST * 100,
                    "note": "Price setup only. No news, no macro, no earnings exclusion."},
           "stats": out_stats, "spy": stats(spy_weekly, spy_weekly), "bySetup": setups}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)

    for key, st in out_stats.items():
        print("%-9s avg/wk %+.3f%%  hit %.1f%%  Sharpe %.2f  maxDD %.1f%%  total %+.1f%%  "
              "(avg %.1f names, %d empty weeks)"
              % (key, st["avgWeek"], st["hitRate"], st["sharpe"], st["maxDD"], st["total"],
                 st["avgNames"], st["emptyWeeks"]))
    sp = out["spy"]
    print("SPY       avg/wk %+.3f%%  Sharpe %.2f  maxDD %.1f%%  total %+.1f%%"
          % (sp["avgWeek"], sp["sharpe"], sp["maxDD"], sp["total"]))
    print("Per setup (gross, per trade; sells shown as the short's return):")
    for k, v in setups.items():
        print("  %-22s %5d trades  avg %+.3f%%  hit %.1f%%" % (k, v["trades"], v["avgPct"], v["hitRate"]))
    print("Wrote", OUT)


if __name__ == "__main__":
    main()
