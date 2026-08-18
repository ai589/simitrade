# 5-year weekly backtest of candidate 1-week NYSE/Nasdaq strategies.
# Rebalance every 5 trading days: rank at close t, enter at close t, exit at close t+5
# (this legacy script; the production rounds in variants*.py default to next-open entry).
# Top 5 picks, equal weight, 0.10% round-trip cost per position.
# Writes strategies.json (stats consumed by screener.py / dashboard.html).
#
# Caveats printed with results: today's universe = survivorship bias; entry at
# signal-day close is optimistic vs real fills. Treat results as relative
# strategy comparison, not absolute return forecasts.
#
# Usage:  python backtest.py

import json
import math
import os
import time
import concurrent.futures
import requests

from screener import UNIVERSE, HEADERS

# Legacy standalone output (nothing reads it; kept for reference in archive/)
STRATEGIES_JSON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "archive", "strategies.json")

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=10y&interval=1d"
# benchmarks that bypass the exchange filter
NONSTOCK = {"SPY", "GC=F", "CL=F"}
COST = 0.001          # round trip per position
TOP_N = 5
HOLD = 5              # trading days
LOOKBACK = 70         # bars of history required before a name is eligible


def fetch5y(sym):
    for attempt in range(3):
        try:
            r = requests.get(CHART_URL.format(sym=sym), headers=HEADERS, timeout=25)
            if r.status_code == 429:
                time.sleep(2 + attempt * 3)
                continue
            r.raise_for_status()
            j = r.json()["chart"]["result"][0]
            if not j["meta"].get("fullExchangeName", "").upper().startswith(("NYSE", "NASDAQ")) and sym not in NONSTOCK:
                return None
            q = j["indicators"]["quote"][0]
            out = {}
            for i, ts in enumerate(j["timestamp"]):
                c, h, v = q["close"][i], q["high"][i], q["volume"][i]
                o = (q.get("open") or [None] * len(q["close"]))[i]
                if c is None:
                    continue
                # (close, high, volume, open) - open added 2026-08 for next-open entry tests
                out[ts // 86400] = (c, h if h is not None else c, v or 0, o if o is not None else c)
            return sym, out
        except Exception:
            if attempt == 2:
                return None
            time.sleep(1 + attempt)
    return None


def rsi(closes, n):
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def features(closes, highs, vols):
    """closes/highs/vols: last LOOKBACK bars up to and including t."""
    px = closes[-1]
    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes[-50:]) / 50
    f = {
        "px": px,
        "ret5": px / closes[-6] - 1,
        "ret20": px / closes[-21] - 1,
        "ret60": px / closes[-61] - 1,
        "sma20": sma20, "sma50": sma50,
        "rsi14": rsi(closes[-30:], 14),
        "rsi3": rsi(closes[-10:], 3),
        "hi20": max(highs[-20:]),
        "volratio": (sum(vols[-5:]) / 5) / max(1e-9, sum(vols[-20:]) / 20),
    }
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 20, len(closes))]
    m = sum(rets) / len(rets)
    f["vol20"] = math.sqrt(sum((x - m) ** 2 for x in rets) / len(rets))
    return f


# ---- Strategy definitions: given features dict per symbol (+ spy), return ranked picks ----

def strat_momentum(feats, spy):
    c = [(s, f) for s, f in feats.items()
         if f["px"] > f["sma20"] > f["sma50"] and (f["rsi14"] or 0) <= 75]
    c.sort(key=lambda x: -(x[1]["ret60"] + x[1]["ret20"]))
    return [s for s, _ in c[:TOP_N]]

def strat_pullback(feats, spy):
    c = [(s, f) for s, f in feats.items()
         if f["px"] > f["sma50"] and f["sma20"] > f["sma50"]
         and f["rsi3"] is not None and f["rsi3"] < 30]
    c.sort(key=lambda x: x[1]["rsi3"])
    return [s for s, _ in c[:TOP_N]]

def strat_breakout(feats, spy):
    c = [(s, f) for s, f in feats.items()
         if f["px"] >= f["hi20"] * 0.998 and f["volratio"] >= 1.1]
    c.sort(key=lambda x: -x[1]["ret20"])
    return [s for s, _ in c[:TOP_N]]

def strat_relstrength(feats, spy):
    if spy is None:
        return []
    c = [(s, f) for s, f in feats.items() if f["px"] > f["sma50"]]
    c.sort(key=lambda x: -(x[1]["ret60"] - spy["ret60"]))
    return [s for s, _ in c[:TOP_N]]

def strat_lowvoltrend(feats, spy):
    c = [(s, f) for s, f in feats.items()
         if f["px"] > f["sma20"] and f["sma20"] > f["sma50"]]
    c.sort(key=lambda x: x[1]["vol20"])
    return [s for s, _ in c[:TOP_N]]

STRATEGIES = {
    "momentum": ("Momentum continuation", strat_momentum,
                 "Uptrend (px>SMA20>SMA50), RSI≤75, ranked by 20d+60d return"),
    "pullback": ("Pullback in uptrend", strat_pullback,
                 "Uptrend names with RSI(3)<30 — buy the short-term dip"),
    "breakout": ("20-day breakout", strat_breakout,
                 "At/above 20d high with volume ≥1.1× average, ranked by 20d return"),
    "relstrength": ("Relative strength vs SPY", strat_relstrength,
                    "Above SMA50, ranked by 60d return minus SPY's 60d return"),
    "lowvoltrend": ("Low-vol trend", strat_lowvoltrend,
                    "Uptrend names ranked by lowest 20d volatility"),
}


def stats(weekly, spy_weekly):
    n = len(weekly)
    wins = sum(1 for w in weekly if w > 0)
    mean = sum(weekly) / n
    sd = math.sqrt(sum((w - mean) ** 2 for w in weekly) / n) or 1e-9
    eq, peak, mdd = 1.0, 1.0, 0.0
    for w in weekly:
        eq *= 1 + w
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    half = n // 2
    beat = sum(1 for w, s in zip(weekly, spy_weekly) if w > s)
    return {
        "weeks": n,
        "hitRate": round(wins / n * 100, 1),
        "avgWeek": round(mean * 100, 3),
        "sharpe": round(mean / sd * math.sqrt(52), 2),
        "maxDD": round(mdd * 100, 1),
        "total": round((eq - 1) * 100, 1),
        "worstWeek": round(min(weekly) * 100, 2),
        "bestWeek": round(max(weekly) * 100, 2),
        "beatSpyPct": round(beat / n * 100, 1),
        "avgWeekH1": round(sum(weekly[:half]) / half * 100, 3),
        "avgWeekH2": round(sum(weekly[half:]) / (n - half) * 100, 3),
    }


def main():
    syms = UNIVERSE + ["SPY"]
    print(f"Fetching 5y history for {len(syms)} symbols...")
    data = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(fetch5y, syms):
            if res:
                data[res[0]] = res[1]
    if "SPY" not in data:
        raise SystemExit("SPY fetch failed — cannot benchmark.")
    print(f"Got {len(data) - 1} names + SPY.")

    # master calendar from SPY
    cal = sorted(data["SPY"].keys())
    idx_of = {d: i for i, d in enumerate(cal)}

    # aligned per-symbol arrays (forward-filled after first obs)
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
    print(f"{len(rebalances)} weekly rebalances from "
          f"{time.strftime('%Y-%m-%d', time.gmtime(cal[rebalances[0]] * 86400))} to "
          f"{time.strftime('%Y-%m-%d', time.gmtime(cal[rebalances[-1]] * 86400))}.")

    weekly = {k: [] for k in STRATEGIES}
    picks_log = {k: [] for k in STRATEGIES}
    spy_weekly = []

    for t in rebalances:
        feats = {}
        for sym, (cl, hi, vo, first) in aligned.items():
            if sym == "SPY" or first > t - LOOKBACK:
                continue
            w_cl = cl[t - LOOKBACK + 1: t + 1]
            w_hi = hi[t - LOOKBACK + 1: t + 1]
            w_vo = vo[t - LOOKBACK + 1: t + 1]
            feats[sym] = features(w_cl, w_hi, w_vo)
        spyf = features(spy_closes[t - LOOKBACK + 1: t + 1],
                        spy_closes[t - LOOKBACK + 1: t + 1],
                        [0] * LOOKBACK)
        spy_weekly.append(spy_closes[t + HOLD] / spy_closes[t] - 1)

        for key, (_, fn, _) in STRATEGIES.items():
            picks = fn(feats, spyf)
            if picks:
                rets = []
                for s in picks:
                    cl = aligned[s][0]
                    rets.append(cl[t + HOLD] / cl[t] - 1 - COST)
                weekly[key].append(sum(rets) / len(rets))
            else:
                weekly[key].append(0.0)  # in cash that week
            picks_log[key].append(len(picks))

    spy_stats = stats(spy_weekly, spy_weekly)
    out = {"generatedFrom": "5y weekly backtest, top 5 equal-weight, 5-day hold, 0.10% cost/position",
           "spy": spy_stats, "strategies": {}}

    print(f"\n{'strategy':<24}{'hit%':>6}{'avg wk':>8}{'sharpe':>8}{'maxDD':>8}"
          f"{'total':>8}{'H1 wk':>8}{'H2 wk':>8}{'>SPY%':>7}")
    print(f"{'SPY (benchmark)':<24}{spy_stats['hitRate']:>6}{spy_stats['avgWeek']:>8}"
          f"{spy_stats['sharpe']:>8}{spy_stats['maxDD']:>8}{spy_stats['total']:>8}"
          f"{spy_stats['avgWeekH1']:>8}{spy_stats['avgWeekH2']:>8}{'':>7}")
    for key, (label, _, desc) in STRATEGIES.items():
        st = stats(weekly[key], spy_weekly)
        st["label"] = label
        st["desc"] = desc
        st["avgPicks"] = round(sum(picks_log[key]) / len(picks_log[key]), 1)
        out["strategies"][key] = st
        print(f"{label:<24}{st['hitRate']:>6}{st['avgWeek']:>8}{st['sharpe']:>8}"
              f"{st['maxDD']:>8}{st['total']:>8}{st['avgWeekH1']:>8}{st['avgWeekH2']:>8}"
              f"{st['beatSpyPct']:>7}")

    with open(STRATEGIES_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print("\nWrote strategies.json")
    print("NOTE: survivorship bias (today's universe) inflates all rows roughly equally;"
          " use for RELATIVE comparison.")


if __name__ == "__main__":
    main()
