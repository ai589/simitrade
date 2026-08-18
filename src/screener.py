# NYSE + Nasdaq 1-Week Swing Trade Screener
# Fetches 2y of daily OHLCV from Yahoo Finance for the S&P 500 + Nasdaq-100 + Dow 30
# universe (see universe.py), scores each name for a 1-week holding period, and writes
# data.js for dashboard.html.
#
# Usage:  python screener.py

import json
import math
import os
import time
import datetime
import concurrent.futures
import requests

# Universe = S&P 500 + Nasdaq-100 + Dow 30 constituents (Wikipedia, weekly cache in
# state/universe.json) + a short pinned EXTRAS list; see universe.py. Listings on any
# other exchange are dropped automatically via the exchange check in main().
# UNIVERSE is Yahoo-form (BRK-B); UNIVERSE_INFO / SECTOR_OF are keyed dot-form (BRK.B).
from universe import load_universe, fold_sector, BUCKETS

UNIVERSE, UNIVERSE_INFO, UNIVERSE_META = load_universe()

# Repo layout: this file lives in src/; generated site data (data.js) stays at
# the repo root for Vercel, mutable state (caches, logs, results) in state/.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=2y&interval=1d"

MIN_DOLLAR_VOL = 50_000_000  # 20-day avg dollar volume floor


def fetch(sym):
    for attempt in range(3):
        try:
            r = requests.get(CHART_URL.format(sym=sym), headers=HEADERS, timeout=20)
            if r.status_code == 429:
                time.sleep(2 + attempt * 3)
                continue
            r.raise_for_status()
            j = r.json()["chart"]["result"][0]
            meta = j["meta"]
            q = j["indicators"]["quote"][0]
            ts = j["timestamp"]
            rows = []
            for i in range(len(ts)):
                c, o, h, l, v = (q["close"][i], q["open"][i], q["high"][i],
                                 q["low"][i], q["volume"][i])
                if c is None or h is None or l is None or v is None:
                    continue
                rows.append({"t": ts[i], "o": o, "h": h, "l": l, "c": c, "v": v})
            return {"symbol": sym,
                    "exchange": meta.get("fullExchangeName", ""),
                    "name": meta.get("longName") or meta.get("shortName") or sym,
                    "rows": rows}
        except Exception as e:
            if attempt == 2:
                print(f"  FAIL {sym}: {e}")
                return None
            time.sleep(1 + attempt)
    return None


def sma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def rsi(closes, n=14):
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
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def atr(rows, n=14):
    if len(rows) < n + 1:
        return None
    trs = []
    for i in range(1, len(rows)):
        h, l, pc = rows[i]["h"], rows[i]["l"], rows[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:n]) / n
    for i in range(n, len(trs)):
        a = (a * (n - 1) + trs[i]) / n
    return a


def analyse(d):
    rows = d["rows"]
    if len(rows) < 70:
        return None
    closes = [r["c"] for r in rows]
    vols = [r["v"] for r in rows]
    px = closes[-1]

    ret_5d = px / closes[-6] - 1
    ret_20d = px / closes[-21] - 1
    ret_60d = px / closes[-61] - 1
    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    r = rsi(closes)
    a = atr(rows)
    hi20 = max(x["h"] for x in rows[-20:])
    dist_hi20 = px / hi20 - 1  # <= 0
    dvol20 = sum(c * v for c, v in zip(closes[-20:], vols[-20:])) / 20
    volratio = (sum(vols[-5:]) / 5) / (sum(vols[-20:]) / 20)
    dailyrets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 20, len(closes))]
    mean = sum(dailyrets) / len(dailyrets)
    vol20 = math.sqrt(sum((x - mean) ** 2 for x in dailyrets) / len(dailyrets)) * math.sqrt(252)

    if None in (sma20, sma50, r, a) or dvol20 < MIN_DOLLAR_VOL:
        return None

    # 12-month value-proxy features (None when history is too short)
    rsi3 = rsi(closes[-10:], 3)
    sma200 = sma(closes, 200)
    hi252 = max(x["h"] for x in rows[-252:]) if len(rows) >= 252 else None
    dd52 = px / hi252 - 1 if hi252 else None
    ret252 = px / closes[-253] - 1 if len(closes) >= 253 else None

    # ---- Composite score for a 1-week hold (0-100) ----
    # Medium-term momentum (trend you're riding): 0-30
    s_mom = max(0.0, min(30.0, (ret_60d * 100) * 1.2 + (ret_20d * 100) * 1.0))
    # Trend alignment: 0-20
    s_trend = 0.0
    if px > sma20:
        s_trend += 8
    if sma20 > sma50:
        s_trend += 7
    if px > sma50:
        s_trend += 5
    # RSI sweet spot for continuation without being overbought: 0-20
    if 50 <= r <= 70:
        s_rsi = 20.0
    elif 45 <= r < 50 or 70 < r <= 75:
        s_rsi = 12.0
    elif r > 75:
        s_rsi = 3.0  # overbought — mean-reversion risk over 1 week
    elif 40 <= r < 45:
        s_rsi = 6.0
    else:
        s_rsi = 0.0
    # Near 20-day high (breakout/continuation setup): 0-15
    s_hi = max(0.0, 15.0 + dist_hi20 * 100 * 2.5)  # -6% away -> 0
    # Volume confirmation: 0-15
    s_vol = max(0.0, min(15.0, (volratio - 0.8) * 25))

    score = round(max(0.0, min(100.0, s_mom + s_trend + s_rsi + s_hi + s_vol)), 1)

    entry = px
    stop = px - 1.5 * a
    target = px + 2.0 * a

    return {
        "sym": d["symbol"].replace("-", "."),
        "name": d["name"],
        "exch": "NASDAQ" if d["exchange"].upper().startswith("NASDAQ") else "NYSE",
        "px": round(px, 2),
        "ret5": round(ret_5d * 100, 2),
        "ret20": round(ret_20d * 100, 2),
        "ret60": round(ret_60d * 100, 2),
        "rsi": round(r, 1),
        "rsi3": round(rsi3, 1) if rsi3 is not None else None,
        "dd52": round(dd52 * 100, 1) if dd52 is not None else None,
        "px200": round(px / sma200 * 100, 1) if sma200 else None,
        "ret252": round(ret252 * 100, 1) if ret252 is not None else None,
        "atr": round(a, 2),
        "atrPct": round(a / px * 100, 2),
        "vol20": round(vol20 * 100, 1),
        "volRatio": round(volratio, 2),
        "distHi20": round(dist_hi20 * 100, 2),
        "dvol": round(dvol20 / 1e6, 1),
        "aboveSma20": px > sma20,
        "aboveSma50": px > sma50,
        "trendUp": sma20 > sma50,
        "score": score,
        "parts": {"mom": round(s_mom, 1), "trend": round(s_trend, 1),
                  "rsi": round(s_rsi, 1), "hi": round(s_hi, 1), "vol": round(s_vol, 1)},
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "spark": [round(c, 2) for c in closes[-60:]],
    }


# Sector buckets (7) — each name's GICS sector folded by universe.py; keyed dot-form.
# variants2/3.py import SECTOR_OF for their sector-neutral backtests.
SECTOR_OF = {s: i["sector7"] for s, i in UNIVERSE_INFO.items()}
SECTORS = {b: sorted(s for s, k in SECTOR_OF.items() if k == b) for b in BUCKETS}


EARN_CACHE = os.path.join(STATE, "earnings_cache.json")
EARN_CACHE_DAYS = 3


def fetch_earnings(symbols):
    """Next earnings date + company profile per symbol via Yahoo quoteSummary
    (cookie+crumb). Cached for EARN_CACHE_DAYS; symbols missing from a fresh cache
    (e.g. after a universe change) are fetched and merged without resetting the TTL.
    Returns ({sym: 'YYYY-MM-DD' or None}, {sym: profile dict})."""
    cache = None
    if os.path.exists(EARN_CACHE):
        try:
            with open(EARN_CACHE, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = None
    fresh = (bool(cache) and "profiles" in cache
             and time.time() - cache.get("_ts", 0) < EARN_CACHE_DAYS * 86400)
    if fresh:
        missing = [s for s in symbols if s not in cache["profiles"]]
        if not missing:
            return cache.get("dates", {}), cache.get("profiles", {})
        print(f"Earnings cache: fetching {len(missing)} symbols not yet cached...")
        d, p = _quote_summary(missing)
        if p:
            cache.setdefault("dates", {}).update(d)
            cache["profiles"].update(p)
            with open(EARN_CACHE, "w", encoding="utf-8") as f:
                json.dump(cache, f)
        return cache.get("dates", {}), cache.get("profiles", {})

    d, p = _quote_summary(symbols)
    if p is None:  # crumb failure: a stale cache beats nothing
        return (cache.get("dates", {}), cache.get("profiles", {})) if cache else ({}, {})
    with open(EARN_CACHE, "w", encoding="utf-8") as f:
        json.dump({"_ts": time.time(), "dates": d, "profiles": p}, f)
    return d, p


def _quote_summary(symbols):
    """Live Yahoo quoteSummary for `symbols`. Returns (dates, profiles), or (None, None)
    when the cookie/crumb handshake fails."""
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        try:
            s.get("https://fc.yahoo.com", timeout=10)
        except requests.RequestException:
            pass  # 404 is fine; we only need the cookie
        crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb",
                      timeout=15).text.strip()
        if not crumb or "<" in crumb:
            raise RuntimeError("no crumb")
    except Exception as e:
        print(f"WARNING: earnings/profile lookup unavailable ({e}); skipping.")
        return None, None

    cookies = s.cookies.get_dict()

    def one(sym):
        raw = sym.replace(".", "-")
        url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{raw}"
        for attempt in range(3):
            try:
                r = requests.get(url, params={
                    "modules": "calendarEvents,assetProfile,summaryDetail,price",
                    "crumb": crumb}, headers=HEADERS, cookies=cookies, timeout=15)
                if r.status_code == 429:
                    time.sleep(2 + attempt * 3)
                    continue
                r.raise_for_status()
                res = r.json()["quoteSummary"]["result"][0]
                dates = (res.get("calendarEvents", {}).get("earnings", {})
                         .get("earningsDate") or [])
                date = dates[0]["fmt"] if dates else None
                ap = res.get("assetProfile", {}) or {}
                sd = res.get("summaryDetail", {}) or {}
                pr = res.get("price", {}) or {}
                fmt = lambda d: (d or {}).get("fmt")
                profile = {
                    "industry": ap.get("industry"),
                    "sector": ap.get("sector"),
                    "employees": ap.get("fullTimeEmployees"),
                    "website": ap.get("website"),
                    "summary": (ap.get("longBusinessSummary") or "")[:600],
                    "mktCap": fmt(pr.get("marketCap")),
                    "pe": fmt(sd.get("trailingPE")),
                    "divYield": fmt(sd.get("dividendYield")),
                    "beta": fmt(sd.get("beta")),
                }
                return sym, date, profile
            except Exception:
                if attempt == 2:
                    return sym, None, {}
                time.sleep(1 + attempt)
        return sym, None, {}

    dates_out, prof_out = {}, {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for sym, d, p in ex.map(one, symbols):
            dates_out[sym] = d
            prof_out[sym] = p
    return dates_out, prof_out


PAPER_LOG = os.path.join(STATE, "picks_log.json")
HOLD_DAYS = 5  # default hold, trading days


def add_trading_days(date_str, n):
    """date_str + n trading days (skips weekends; US holidays not modelled,
    so treat as 'sell on or around this date')."""
    d = datetime.date.fromisoformat(date_str)
    while n > 0:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d.isoformat()


def update_paper_log(datamap, strategies, results, today_str):
    """Log today's strategy picks and evaluate matured ones against actual prices.
    datamap: normalized sym -> list of {t,o,h,l,c,v} daily bars (6mo).
    Exit rules per trade: stop hit (day low <= stop, filled at stop; checked before
    target on the same bar - conservative), target hit (day high >= target, filled
    at target), else close of the HOLD_DAYS-th bar after entry."""
    log = {"entries": []}
    if os.path.exists(PAPER_LOG):
        try:
            with open(PAPER_LOG, encoding="utf-8") as f:
                log = json.load(f)
        except Exception:
            pass

    def day_str(ts):
        return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")

    # evaluate open entries
    for e in log["entries"]:
        if e.get("status") != "open":
            continue
        bars = datamap.get(e["sym"])
        if not bars:
            continue
        idx = next((i for i, b in enumerate(bars) if day_str(b["t"]) == e["date"]), None)
        if idx is None:
            continue
        hd = e.get("holdDays", HOLD_DAYS)
        short = e.get("side") == "short"
        for k, b in enumerate(bars[idx + 1: idx + 1 + hd], start=1):
            hit_stop = (b["h"] is not None and b["h"] >= e["stop"]) if short \
                else (b["l"] is not None and b["l"] <= e["stop"])
            if hit_stop:
                e.update(status="stopped", exitDate=day_str(b["t"]),
                         exitPx=e["stop"], days=k)
                break
            hit_target = (b["l"] is not None and b["l"] <= e["target"]) if short \
                else (b["h"] is not None and b["h"] >= e["target"])
            if hit_target:
                e.update(status="target", exitDate=day_str(b["t"]),
                         exitPx=e["target"], days=k)
                break
        else:
            after = bars[idx + 1: idx + 1 + hd]
            if len(after) >= hd:
                b = after[-1]
                e.update(status="time-exit", exitDate=day_str(b["t"]),
                         exitPx=round(b["c"], 2), days=hd)
        if e.get("exitPx"):
            raw = e["exitPx"] / e["entry"] - 1
            e["retPct"] = round((-raw if short else raw) * 100, 2)

    # log today's picks (idempotent per date+strategy)
    already = {(e["date"], e["strategy"], e["sym"]) for e in log["entries"]}
    by_sym = {r["sym"]: r for r in results}
    for st in strategies:
        hd = st.get("holdDays", HOLD_DAYS)
        sm, tm = st.get("stopMult", 1.5), st.get("targetMult", 2.0)
        side = st.get("side", "long")
        for sym in st["picks"]:
            r = by_sym.get(sym)
            if not r or (today_str, st["key"], sym) in already:
                continue
            atr = r.get("atr")
            if atr and side == "short":
                stop = round(r["entry"] + sm * atr, 2)
                target = round(r["entry"] - tm * atr, 2)
            elif atr:
                stop = round(r["entry"] - sm * atr, 2)
                target = round(r["entry"] + tm * atr, 2)
            else:
                stop, target = r["stop"], r["target"]
            log["entries"].append({
                "date": today_str, "strategy": st["key"], "sym": sym,
                "entry": r["entry"], "stop": stop, "target": target, "side": side,
                "holdDays": hd, "sellBy": add_trading_days(today_str, hd),
                "status": "open",
            })

    # backfill sell-by dates on older open entries
    for e in log["entries"]:
        if e["status"] == "open" and "sellBy" not in e:
            e["sellBy"] = add_trading_days(e["date"], e.get("holdDays", HOLD_DAYS))

    with open(PAPER_LOG, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=1)

    # aggregate closed-trade stats per strategy
    agg = {}
    closed_rets = {}  # strategy -> [(date, retPct)] for the recent-window stats
    for e in log["entries"]:
        a = agg.setdefault(e["strategy"], {"open": 0, "closed": 0, "wins": 0,
                                           "sumRet": 0.0, "trades": []})
        if e["status"] == "open":
            a["open"] += 1
        else:
            a["closed"] += 1
            a["wins"] += 1 if e.get("retPct", 0) > 0 else 0
            a["sumRet"] += e.get("retPct", 0)
            closed_rets.setdefault(e["strategy"], []).append((e["date"], e.get("retPct", 0)))
        a["trades"].append(e)
    for key, a in agg.items():
        a["hitRate"] = round(a["wins"] / a["closed"] * 100, 1) if a["closed"] else None
        a["avgRet"] = round(a["sumRet"] / a["closed"], 2) if a["closed"] else None
        a["trades"] = sorted(a["trades"], key=lambda x: x["date"], reverse=True)[:40]
        # last RECENT_N closed trades - the early-warning window for the health check
        rec = [r for _, r in sorted(closed_rets.get(key, []))[-RECENT_N:]]
        a["recent"] = {"n": len(rec),
                       "avgRet": round(sum(rec) / len(rec), 2) if rec else None,
                       "hitRate": round(sum(1 for r in rec if r > 0) / len(rec) * 100, 1) if rec else None}
    # names still open from an earlier session, per strategy — powers the
    # hold / new / rotate-out view on the strategy cards (earliest entry wins,
    # since that is when the human actually bought)
    for e in log["entries"]:
        if e["status"] != "open" or e["date"] >= today_str or e["strategy"] not in agg:
            continue
        ho = agg[e["strategy"]].setdefault("heldOpen", {})
        cur = ho.get(e["sym"])
        if cur is None or e["date"] < cur["since"]:
            ho[e["sym"]] = {"since": e["date"], "sellBy": e.get("sellBy")}
    return agg


RECENT_N = 60      # closed trades in the "recent" live window
MIN_LIVE = 30      # closed trades before the live record is judged at all


def strategy_health(st, paper_agg):
    """Live paper-trade record vs backtest expectation - the kill switch.
    Compares avg % per trade in the paper log (real fills incl. stops/targets) with the
    backtest's avg % per hold. Rules, deliberately simple and transparent:
      research - backtest itself is <= 0 (the shorts): not for live use, whatever live says
      young    - fewer than MIN_LIVE closed live trades: no verdict yet
      demoted  - >= MIN_LIVE closed and live avg < 0, or the recent window (>= MIN_LIVE
                 trades) is negative while the backtest is positive
      watch    - live avg < 50% of the backtest avg
      ok       - otherwise"""
    a = paper_agg.get(st["key"]) or {}
    bt = st.get("stats") or {}
    bt_avg = bt.get("avgWeek")
    closed = a.get("closed", 0)
    live_avg = a.get("avgRet")
    rec = a.get("recent") or {}
    h = {"liveN": closed, "liveAvg": live_avg, "liveHit": a.get("hitRate"),
         "recentN": rec.get("n", 0), "recentAvg": rec.get("avgRet"), "btAvg": bt_avg,
         "ratio": (round(live_avg / bt_avg, 2) if live_avg is not None and bt_avg else None)}
    if bt_avg is None:
        h.update(status="young", note="No backtest stats.")
    elif bt_avg <= 0:
        h.update(status="research", note="Backtest is negative - research only, not for live money.")
    elif closed < MIN_LIVE:
        h.update(status="young", note=f"{closed}/{MIN_LIVE} closed live trades - no verdict yet.")
    elif live_avg < 0 or (rec.get("n", 0) >= MIN_LIVE and (rec.get("avgRet") or 0) < 0):
        h.update(status="demoted",
                 note=f"Live {live_avg:+.2f}%/trade (last {rec.get('n')}: {rec.get('avgRet'):+.2f}%) "
                      f"vs backtest {bt_avg:+.2f}% - losing live; stop trading it until it recovers.")
    elif live_avg < 0.5 * bt_avg:
        h.update(status="watch",
                 note=f"Live {live_avg:+.2f}%/trade is under half the backtest {bt_avg:+.2f}% - "
                      "size down; demote if it turns negative.")
    else:
        h.update(status="ok",
                 note=f"Live {live_avg:+.2f}%/trade vs backtest {bt_avg:+.2f}% - tracking.")
    return h


def strategy_picks(results, regime_on, last_session):
    """Live picks for the three backtested strategies (see variants.py)."""
    # Exclude names reporting earnings during the hold window (event risk the
    # backtest could not model). Baseline momentum universe: px > SMA20 > SMA50, RSI <= 75.
    tradeable = [r for r in results if not r.get("earnSoon")]
    mom_pool = [r for r in tradeable if r["aboveSma20"] and r["trendUp"]
                and r["aboveSma50"] and r["rsi"] <= 75]
    mom_ranked = sorted(mom_pool, key=lambda r: -(r["ret60"] + r["ret20"]))
    lowvol_pool = sorted([r for r in tradeable if r["aboveSma20"] and r["trendUp"]],
                         key=lambda r: r["vol20"])
    # sector-neutral momentum: best momentum name in each sector (round-3 winner)
    sector_best = {}
    for r in mom_ranked:  # already sorted by momentum desc
        sector_best.setdefault(r["sector"], r)
    sector_picks = [r["sym"] for r in sector_best.values()]
    # weekly dip: uptrend names with the worst 5-day return (short-term reversal)
    dip_pool = sorted([r for r in tradeable if r["aboveSma50"] and r["trendUp"]],
                      key=lambda r: r["ret5"])
    # 14-day hold needs a wider earnings exclusion window (14 days, not 7)
    mom14_pool = [r for r in mom_ranked
                  if r.get("earnDays") is None or not (0 <= r["earnDays"] <= 14)]
    # 14d sector-neutral: best momentum name per sector, 14-day earnings window
    sector_best14 = {}
    for r in mom14_pool:
        sector_best14.setdefault(r["sector"], r)
    sector14_picks = [r["sym"] for r in sector_best14.values()]
    # value proxies: cheap vs the stock's own 12-month history
    value_dd_pool = sorted([r for r in tradeable if r.get("dd52") is not None],
                           key=lambda r: r["dd52"])
    value_200_pool = sorted(
        [r for r in tradeable if r.get("px200") is not None
         and r["px200"] < 100 and r["aboveSma20"]],
        key=lambda r: r["px200"])
    # momentum / low-vol z-blend: names above SMA50 scored by momentum z minus
    # volatility z — high momentum with the least chop (best Sharpe of all
    # weekly rule sets tested; promoted to replace the retired shortRally)
    zpool = [r for r in tradeable if r["aboveSma50"]]
    zblend_ranked = []
    if len(zpool) >= 2:
        def _z(vals):
            m = sum(vals) / len(vals)
            sd = math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals)) or 1e-9
            return [(v - m) / sd for v in vals]
        zm = _z([r["ret60"] + r["ret20"] for r in zpool])
        zv = _z([r["vol20"] for r in zpool])
        zblend_ranked = [r for _, r in sorted(
            zip([a - b for a, b in zip(zm, zv)], zpool),
            key=lambda p: -p[0])]
    # short pool (deployed only in a bear regime: SPY < 50-day MA)
    short_spike_pool = sorted([r for r in tradeable if not r["aboveSma50"]],
                              key=lambda r: -r["ret5"])
    # overvalued shorts (always in; price proxies for "overvalued" — the two
    # winners of the round-5 overvalued-short tests, judged on bear-regime weeks)
    short_blowoff_pool = sorted([r for r in tradeable if r["rsi"] >= 80],
                                key=lambda r: -r["ret20"])
    short_overext_pool = sorted(
        [r for r in tradeable if r.get("px200") is not None
         and r["px200"] > 120 and r["rsi"] >= 75],
        key=lambda r: -r["px200"])

    stats_map = {}
    curves = None
    try:
        with open(os.path.join(STATE, "variants_results.json"), encoding="utf-8") as f:
            vr = json.load(f)
        stats_map = {
            "momTop10": vr["variants"].get("mom top10 + regime"),
            "momTop5": vr["variants"].get("mom (baseline)"),
            "lowvol": vr["variants"].get("lowvol + regime"),
            "sectorNeutral": vr["variants"].get("sector-neutral mom + regime"),
            "weeklyDip": vr["variants"].get("weekly dip in uptrend"),
            "mom14": vr.get("variants14", {}).get("14d mom top5"),
            "sector14": vr.get("variants14", {}).get("14d sector-neutral + regime"),
            "valueDD": vr.get("variantsExtra", {}).get("deep 52w drawdown"),
            "value200": vr.get("variantsExtra", {}).get("below 200d MA + turning up"),
            "zblend": vr["variants"].get("mom/low-vol z-blend + regime"),
            "shortSpike": vr.get("variantsExtra", {}).get("short 5d spike + bear regime"),
            "shortBlowoff": vr.get("variantsExtra", {}).get("short RSI blow-off"),
            "shortOverext": vr.get("variantsExtra", {}).get("short overbought extension"),
            "spy": vr.get("spy"),
        }
        c = vr.get("curves")
        if c:
            curves = {"dates": c["dates"], "spy": c["spy"],
                      "momTop10": c.get("mom top10 + regime"),
                      "momTop5": c.get("mom (baseline)"),
                      "lowvol": c.get("lowvol + regime"),
                      "gold": c.get("gold"), "oil": c.get("oil")}
            c14 = vr.get("curves14")
            if c14:
                curves["dates14"] = c14["dates"]
                curves["mom14"] = c14.get("14d mom top5")
                curves["sector14"] = c14.get("14d sector-neutral + regime")
            cex = vr.get("curvesExtra")
            if cex:
                curves["datesExtra"] = cex["dates"]
                curves["valueDD"] = cex.get("deep 52w drawdown")
                curves["value200"] = cex.get("below 200d MA + turning up")
                curves["shortSpike"] = cex.get("short 5d spike + bear regime")
                curves["shortBlowoff"] = cex.get("short RSI blow-off")
                curves["shortOverext"] = cex.get("short overbought extension")
                curves["bearFlags"] = cex.get("bearFlags")
            c2 = vr.get("curves2")
            if c2:
                curves["dates2"] = c2["dates"]
                curves["zblend"] = c2.get("mom/low-vol z-blend + regime")
                curves["sectorNeutral"] = c2.get("sector-neutral mom + regime")
                curves["weeklyDip"] = c2.get("weekly dip in uptrend")
    except Exception:
        pass

    sell5 = add_trading_days(last_session, 5)
    sell10 = add_trading_days(last_session, 10)
    common5 = {"holdDays": 5, "stopMult": 1.5, "targetMult": 2.0, "sellBy": sell5}
    return [dict(s) for s in [
        {**common5, "key": "momTop10", "label": "Momentum Top-10 + regime",
         "desc": "Uptrend (px>SMA20>SMA50, RSI≤75) ranked by 20d+60d return, "
                 "10 names. Cash when SPY < 50-day MA.",
         "regimeGated": True,
         "picks": [r["sym"] for r in mom_ranked[:10]] if regime_on else [],
         "stats": stats_map.get("momTop10")},
        {**common5, "key": "momTop5", "label": "Momentum Top-5 (always in)",
         "desc": "Same ranking, concentrated in 5 names, no regime filter. "
                 "Higher return, deeper drawdowns.",
         "regimeGated": False,
         "picks": [r["sym"] for r in mom_ranked[:5]],
         "stats": stats_map.get("momTop5")},
        {**common5, "key": "lowvol", "label": "Low-vol trend + regime",
         "desc": "Uptrend names ranked by lowest 20d volatility, 5 names. "
                 "Cash when SPY < 50-day MA. Defensive: smallest drawdowns.",
         "regimeGated": True,
         "picks": [r["sym"] for r in lowvol_pool[:5]] if regime_on else [],
         "stats": stats_map.get("lowvol")},
        {**common5, "key": "sectorNeutral", "label": "Sector-neutral momentum + regime",
         "desc": "Best momentum name in each sector (~7 names), uptrend + RSI filter. "
                 "Cash when SPY < 50-day MA. Best risk-adjusted in testing (3.8y sample).",
         "regimeGated": True,
         "picks": sector_picks if regime_on else [],
         "stats": stats_map.get("sectorNeutral")},
        {**common5, "key": "weeklyDip", "label": "Weekly dip in uptrend (always in)",
         "desc": "Uptrend names (px>SMA50, SMA20>SMA50) with the worst 5-day return — "
                 "buy the weekly dip, 5 names, no regime filter (3.8y sample).",
         "regimeGated": False,
         "picks": [r["sym"] for r in dip_pool[:5]],
         "stats": stats_map.get("weeklyDip")},
        {"key": "mom14", "label": "14-day Momentum Top-5 (always in)",
         "desc": "Same momentum ranking, held ~14 calendar days (10 trading days), "
                 "5 names, no regime filter. Wider levels: stop 2×ATR, target 3×ATR. "
                 "Best 2-week hold in testing, but expect deeper drawdowns "
                 "(-35% max in backtest). Stats are per 2-week period.",
         "regimeGated": False, "holdDays": 10,
         "stopMult": 2.0, "targetMult": 3.0, "sellBy": sell10,
         "picks": [r["sym"] for r in mom14_pool[:5]],
         "stats": stats_map.get("mom14")},
        {"key": "sector14", "label": "14-day Sector-neutral momentum + regime",
         "desc": "The 10-year champion: best momentum name per sector (~7 names), "
                 "held ~14 calendar days, cash when SPY < 50-day MA. The only strategy "
                 "to beat SPY on both return and drawdown over 10 years. "
                 "Stats are per 2-week period.",
         "regimeGated": True, "holdDays": 10,
         "stopMult": 2.0, "targetMult": 3.0, "sellBy": sell10,
         "picks": sector14_picks if regime_on else [],
         "stats": stats_map.get("sector14")},
        {**common5, "key": "valueDD", "label": "Value: deep 52-week drawdown",
         "desc": "The 5 names furthest below their own 52-week high — buying liquid "
                 "large-caps when they are cheapest vs their own history. No trend "
                 "filter (deliberately catches falling knives; the liquid universe is "
                 "what makes that survivable). Price-proxy value: no P/E data in backtest.",
         "regimeGated": False,
         "picks": [r["sym"] for r in value_dd_pool[:5]],
         "stats": stats_map.get("valueDD")},
        {**common5, "key": "value200", "label": "Value: below 200-day MA, turning up",
         "desc": "Names below their 200-day average (cheap vs long-run trend) that "
                 "have reclaimed their 20-day average (already bouncing), ranked "
                 "cheapest first. The gentler value entry.",
         "regimeGated": False,
         "picks": [r["sym"] for r in value_200_pool[:5]],
         "stats": stats_map.get("value200")},
        {**common5, "key": "zblend", "label": "Momentum / low-vol z-blend + regime",
         "desc": "Names above their 50-day MA scored by momentum z-score (20d+60d "
                 "return) minus volatility z-score — the strongest movers with the "
                 "least chop, 5 names. Cash when SPY < 50-day MA. Replaced the "
                 "retired short-rally strategy (-64% backtest).",
         "regimeGated": True,
         "picks": [r["sym"] for r in zblend_ranked[:5]] if regime_on else [],
         "stats": stats_map.get("zblend")},
        {**common5, "key": "shortSpike", "label": "Short: 5-day spike below SMA50",
         "side": "short", "bearGated": True,
         "desc": "SHORT the sharpest 5-day spikes among names below their 50-day MA. "
                 "Only active when SPY < 50-day MA. WARNING: lost 67% over the full "
                 "backtest — informational; shorting weekly was a losing game in this sample.",
         "regimeGated": False,
         "picks": [r["sym"] for r in short_spike_pool[:5]] if not regime_on else [],
         "stats": stats_map.get("shortSpike")},
        {**common5, "key": "shortBlowoff", "label": "Short: RSI blow-off (always in)",
         "side": "short",
         "desc": "SHORT the fastest 20-day gainers with RSI-14 ≥ 80 — parabolic "
                 "blow-offs, the classic overbought/overvalued proxy. Judge it on the "
                 "bear-weeks row below, not the full sample: shorting a decade-long "
                 "bull loses by design; whatever edge this rule has shows only in "
                 "SPY<50-day-MA weeks (one of 6 overvalued-short rules tested).",
         "regimeGated": False,
         "emptyNote": "No names with RSI-14 ≥ 80 this week — blow-offs are rare "
                      "outside manias, so this strategy is often flat.",
         "picks": [r["sym"] for r in short_blowoff_pool[:5]],
         "stats": stats_map.get("shortBlowoff")},
        {**common5, "key": "shortOverext", "label": "Short: overbought extension (always in)",
         "side": "short",
         "desc": "SHORT names stretched ≥20% above their own 200-day MA with "
                 "RSI-14 ≥ 75 — the most extended and overbought, i.e. richest vs "
                 "their own trend. Judge it on the bear-weeks row below: full-sample "
                 "shorting loses; whatever edge this rule has shows only in "
                 "SPY<50-day-MA weeks (one of 6 overvalued-short rules tested).",
         "regimeGated": False,
         "emptyNote": "No names ≥20% above their 200-day MA with RSI ≥ 75 this "
                      "week — extreme extensions have cooled off.",
         "picks": [r["sym"] for r in short_overext_pool[:5]],
         "stats": stats_map.get("shortOverext")},
    ]], stats_map.get("spy"), curves


def open_paper_syms():
    """Symbols with an open paper trade in picks_log.json (dot-form)."""
    try:
        with open(PAPER_LOG, encoding="utf-8") as f:
            return {e["sym"] for e in json.load(f)["entries"] if e.get("status") == "open"}
    except Exception:
        return set()


def main():
    c = UNIVERSE_META.get("counts", {})
    print(f"Universe: {len(UNIVERSE)} names (S&P {c.get('spx')} | NDX {c.get('ndx')} | "
          f"DJI {c.get('dji')} | extras {c.get('extras')}; list as of {UNIVERSE_META.get('date')})")
    print(f"Fetching {len(UNIVERSE)} symbols...")
    results = []
    skipped_exchange = []
    datamap = {}  # normalized sym -> daily bars, for paper-trade evaluation
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for d in ex.map(fetch, UNIVERSE):
            if d is None:
                continue
            if not d["exchange"].upper().startswith(("NYSE", "NASDAQ")):
                skipped_exchange.append(f'{d["symbol"]} ({d["exchange"]})')
                continue
            row = analyse(d)
            if row:
                row["sector"] = SECTOR_OF.get(row["sym"], "Other")
                row["ix"] = UNIVERSE_INFO.get(row["sym"], {}).get("ix", [])
                results.append(row)
                datamap[row["sym"]] = d["rows"]

    # Open paper trades on names that have since left the universe (index rebalance,
    # liquidity dip) still need bars so they can close normally instead of dangling.
    orphans = sorted(s for s in open_paper_syms()
                     if s != "SPY" and s not in datamap and s.replace(".", "-") not in UNIVERSE)
    if orphans:
        print(f"Fetching {len(orphans)} open paper-trade names no longer in universe: "
              + ", ".join(orphans))
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            for d in ex.map(fetch, [s.replace(".", "-") for s in orphans]):
                if d and d["rows"]:
                    datamap[d["symbol"].replace("-", ".")] = d["rows"]  # bars only; not screened

    # Safety guard for unattended runs: if the fetch came back badly degraded,
    # keep yesterday's data.js rather than overwriting it with a partial screen.
    min_ok = int(0.6 * len(UNIVERSE))
    if len(results) < min_ok:
        raise SystemExit(f"ABORT: only {len(results)} of {len(UNIVERSE)} names screened "
                         f"(floor {min_ok}). Keeping previous data.js.")

    # earnings dates + company profiles
    print("Fetching earnings dates and company profiles...")
    earn, profiles = fetch_earnings([r["sym"] for r in results])
    # Any name still unbucketed (no GICS/hand/ICB sector) takes its Yahoo profile sector.
    for r in results:
        if r["sector"] == "Other":
            r["sector"] = fold_sector((profiles.get(r["sym"]) or {}).get("sector")) or "Other"
    today = datetime.date.today()
    n_soon = 0
    for r in results:
        ed = earn.get(r["sym"])
        r["earnDate"] = ed
        r["earnDays"] = None
        r["earnSoon"] = False
        if ed:
            try:
                days = (datetime.date.fromisoformat(ed) - today).days
                r["earnDays"] = days
                r["earnSoon"] = 0 <= days <= 7
                n_soon += r["earnSoon"]
            except ValueError:
                pass
    print(f"{n_soon} names report earnings within 7 days (excluded from strategy picks).")

    # SPY for the regime filter
    spy = fetch("SPY")
    regime_on, spy_px, spy_sma50 = True, None, None
    if spy and len(spy["rows"]) >= 50:
        spy_closes = [r["c"] for r in spy["rows"]]
        spy_px = spy_closes[-1]
        spy_sma50 = sum(spy_closes[-50:]) / 50
        regime_on = spy_px > spy_sma50
    else:
        print("WARNING: SPY fetch failed; assuming regime ON.")

    if skipped_exchange:
        print("Skipped (other exchange):", ", ".join(skipped_exchange))

    results.sort(key=lambda x: -x["score"])
    n_above20 = sum(1 for r in results if r["aboveSma20"])
    breadth = round(n_above20 / len(results) * 100, 1) if results else 0
    avg5 = round(sum(r["ret5"] for r in results) / len(results), 2) if results else 0

    # entry date = last completed session in the data
    last_session = None
    if datamap:
        any_rows = next(iter(datamap.values()))
        last_session = datetime.datetime.utcfromtimestamp(
            any_rows[-1]["t"]).strftime("%Y-%m-%d")

    strategies, spy_stats, curves = strategy_picks(
        results, regime_on, last_session or today.isoformat())

    # paper-trade log; SPY is logged alongside as the benchmark (no stops)
    log_strategies = list(strategies)
    log_results = list(results)
    if spy and spy_px:
        datamap["SPY"] = spy["rows"]
        log_strategies.append({"key": "spy", "picks": ["SPY"]})
        log_results.append({"sym": "SPY", "entry": round(spy_px, 2),
                            "stop": 0.0, "target": 9e9})
    paper = update_paper_log(datamap, log_strategies, log_results,
                             last_session or today.isoformat())
    n_open = sum(a["open"] for a in paper.values())
    n_closed = sum(a["closed"] for a in paper.values())
    print(f"Paper log: {n_open} open, {n_closed} closed trades.")

    # Weekly rollover view: a pick already open from an earlier session is a HOLD
    # (keep it — selling and re-buying the same close is the backtest's implicit
    # behaviour, minus the costs); open names no longer picked should rotate out.
    for st in strategies:
        ho = (paper.get(st["key"]) or {}).get("heldOpen") or {}
        st["held"] = {s: ho[s]["since"] for s in st["picks"] if s in ho}
        st["rotateOut"] = [
            {"sym": s, "since": v["since"], "sellBy": v.get("sellBy")}
            for s, v in sorted(ho.items()) if s not in st["picks"]]
        st["health"] = strategy_health(st, paper)
    flagged = [f'{st["key"]}={st["health"]["status"]}' for st in strategies
               if st["health"]["status"] in ("watch", "demoted")]
    print("Strategy health: " + (", ".join(flagged) if flagged else "no live-vs-backtest flags."))
    bt_meta = {}
    try:
        with open(os.path.join(STATE, "variants_results.json"), encoding="utf-8") as f:
            bt_meta = json.load(f).get("meta") or {}
    except Exception:
        pass

    payload = {
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "asOfNote": "Prices as of last completed US session",
        "count": len(results),
        "breadth20": breadth,
        "avgRet5": avg5,
        "regimeOn": regime_on,
        "spyPx": round(spy_px, 2) if spy_px else None,
        "spySma50": round(spy_sma50, 2) if spy_sma50 else None,
        "strategies": strategies,
        "btMeta": bt_meta,
        "spyStats": spy_stats,
        "curves": curves,
        "paper": paper,
        # only the screened names — the cache accumulates names that have since dropped out
        "profiles": {r["sym"]: profiles[r["sym"]] for r in results if r["sym"] in profiles},
        "rows": results,
    }
    out = os.path.join(ROOT, "data.js")
    # Dropbox can briefly lock the file mid-sync (OSError 22) — retry a few times.
    for attempt in range(5):
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write("window.SCREEN_DATA = ")
                json.dump(payload, f)
                f.write(";")
            break
        except OSError:
            if attempt == 4:
                raise
            time.sleep(2 * (attempt + 1))
    print(f"Wrote {out}: {len(results)} names, breadth {breadth}% above SMA20.")
    print("Top 10:", ", ".join(f'{r["sym"]}({r["score"]})' for r in results[:10]))


if __name__ == "__main__":
    main()
