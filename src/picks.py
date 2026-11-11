# Top Buys / Top Sells: the two lists the tool leads with.
#
# Every pick's score is the sum of three visible parts:
#
#   setup  0..100   price action only - which short-term pattern the stock is in and how
#                   clean it is (the only part that can be backtested)
#   news   -20..+20 what the headlines add (Claude's read; halved for the keyword fallback)
#   macro  -15..+15 sector tailwind/headwind plus the overall risk backdrop
#
# For sells the news and macro parts are mirrored: bad news and a risk-off tape ADD to a
# sell score.
#
# The buy setups lean on what this project's own record supports: the mean-reversion
# ideas (dip inside an uptrend, cheap vs its own history and turning up) score highest,
# pure momentum is capped lower because it has been losing live. The sell setups are
# research-grade - every short rule tested here lost money over ten years - so the sell
# list is a watch / avoid / trim list first and a shorting list a distant second.
#
# What src/picks_backtest.py says about the setups alone (2017-2026, no news or macro):
# the buy list averaged +0.49% a week against SPY's +0.26%; the sell list LOST 0.20% a
# week as a short. Sell-list names still rose on average - just by less than the market -
# which is why it is an avoid/trim list and not a shorting list. One attempt to re-weight
# the sell setups from those results made them worse out of the box and was reverted:
# don't tune these weights to the backtest.
#
# Pure functions over the row dicts from screener.analyse(); no network, no screener
# import. Tested in tests/test_picks.py.

SHORTLIST_N = 25
TOP_N = 8
MAX_PER_SECTOR = 2
MIN_SCORE = 50.0
STOP_MULT, TARGET_MULT, HOLD_DAYS = 1.5, 2.0, 5

NEWS_MAX = 20.0
KEYWORD_WEIGHT = 0.5      # the keyword fallback is crude, so it moves scores half as far
SECTOR_MAX = 8.0
REGIME_MAX = 7.0


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _atr_pct(r):
    return max(r.get("atrPct") or 0, 0.5)


def bull_setups(r):
    """Every buy pattern this row qualifies for, as (score, label, reasons)."""
    out = []
    a = _atr_pct(r)
    px200, dd52 = r.get("px200"), r.get("dd52")

    # 1. Dip inside an uptrend: trend intact, the last week sold off. Dip is measured in
    #    ATRs so a 4% drop in a sleepy stock counts for more than 4% in a wild one.
    if r["aboveSma50"] and r["trendUp"] and r["ret5"] < 0:
        dip = -r["ret5"] / a
        s = 45 + 16 * clamp(dip / 2.5) * 2.5
        why = ["uptrend intact (above 50-day, 20-day above 50-day)",
               "down %.1f%% in 5 days (%.1f ATRs)" % (-r["ret5"], dip)]
        if r.get("rsi3") is not None and r["rsi3"] < 20:
            s += 5
            why.append("3-day RSI %.0f - washed out short term" % r["rsi3"])
        if px200 is not None and px200 > 100:
            s += 5
            why.append("above its 200-day average")
        out.append((min(s, 95.0), "Dip in an uptrend", why))

    # 2. Value turning up: below the 200-day (cheap vs its own trend) but back above the
    #    20-day (already bouncing).
    if px200 is not None and px200 < 100 and r["aboveSma20"]:
        s = 45 + 22 * clamp((100 - px200) / 20)
        why = ["%.0f%% below its 200-day average" % (100 - px200),
               "back above its 20-day average"]
        if dd52 is not None:
            s += 13 * clamp(-dd52 / 40)
            why.append("%.0f%% off its 52-week high" % -dd52)
        if r["ret5"] > 0:
            s += 5
        if r["volRatio"] > 1.1:
            s += 5
            why.append("volume picking up (%.1fx)" % r["volRatio"])
        out.append((min(s, 90.0), "Value turning up", why))

    # 3. Deep drawdown: far off the 52-week high. Deliberately catches falling knives, so
    #    it is capped below the two setups above and wants some sign of a floor.
    if dd52 is not None and dd52 <= -30:
        s = 38 + 22 * clamp((-dd52 - 30) / 30)
        why = ["%.0f%% off its 52-week high" % -dd52]
        if r["rsi"] < 35:
            s += 8
            why.append("RSI %.0f - oversold" % r["rsi"])
        if r["aboveSma20"]:
            s += 10
            why.append("back above its 20-day average")
        out.append((min(s, 78.0), "Deep drawdown", why))

    # 4. Momentum continuation: capped lowest - it is the textbook setup, and the one
    #    that has lost money in this project's live record.
    if (r["aboveSma20"] and r["aboveSma50"] and r["trendUp"]
            and 50 <= r["rsi"] <= 70 and r["distHi20"] > -3):
        s = 35 + 22 * clamp((r["ret20"] + r["ret60"]) / 40)
        why = ["strong trend (20d %+.0f%%, 60d %+.0f%%)" % (r["ret20"], r["ret60"]),
               "within %.1f%% of its 20-day high, RSI %.0f" % (-r["distHi20"], r["rsi"])]
        if r["volRatio"] > 1.2:
            s += 6
            why.append("volume confirming (%.1fx)" % r["volRatio"])
        out.append((min(s, 65.0), "Momentum", why))
    return out


def bear_setups(r):
    """Every sell pattern this row qualifies for, as (score, label, reasons)."""
    out = []
    a = _atr_pct(r)
    px200 = r.get("px200")

    # 1. Breakdown: below both averages, short-term average under the long one, still
    #    falling. Marked down when already deeply oversold (bounce risk).
    if not r["aboveSma50"] and not r["aboveSma20"] and not r["trendUp"] and r["ret20"] < 0:
        s = 48 + 18 * clamp(-r["ret20"] / (4 * a))
        why = ["below its 20-day and 50-day averages, trend down",
               "down %.1f%% in 20 days" % -r["ret20"]]
        if px200 is not None and px200 < 100:
            s += 9
            why.append("below its 200-day average")
        if r["volRatio"] > 1.2:
            s += 9
            why.append("selling on rising volume (%.1fx)" % r["volRatio"])
        if r["rsi"] < 30:
            s -= 15
            why.append("but RSI %.0f is already oversold - bounce risk" % r["rsi"])
        elif r.get("rsi3") is not None and r["rsi3"] < 10:
            s -= 8
            why.append("but 3-day RSI %.0f - short-term bounce risk" % r["rsi3"])
        out.append((clamp(s, 0, 90.0), "Breakdown", why))

    # 2. Failed bounce: in a downtrend, has just rallied back up into it.
    if (not r["aboveSma50"] and not r["trendUp"] and r["ret5"] > 0
            and 40 <= r["rsi"] <= 58 and (px200 is None or px200 < 100)):
        s = 48 + 24 * clamp(r["ret5"] / (2 * a))
        why = ["downtrend (below 50-day, 20-day under 50-day)",
               "bounced %.1f%% in 5 days into resistance" % r["ret5"]]
        if r["volRatio"] < 0.9:
            s += 8
            why.append("bounce on thin volume (%.1fx)" % r["volRatio"])
        out.append((min(s, 85.0), "Bounce in a downtrend", why))

    # 3. Blow-off: stretched far above its own trend and very overbought.
    if px200 is not None and r["rsi"] >= 78 and px200 >= 115:
        s = 48 + 18 * clamp((r["rsi"] - 78) / 12) + 18 * clamp((px200 - 115) / 25)
        why = ["RSI %.0f - very overbought" % r["rsi"],
               "%.0f%% above its 200-day average" % (px200 - 100)]
        if r["ret5"] > 3 * a:
            s += 6
            why.append("up %.1f%% in 5 days - parabolic" % r["ret5"])
        out.append((min(s, 88.0), "Blow-off", why))
    return out


def _best(setups):
    if not setups:
        return 0.0, None, []
    s, label, why = max(setups, key=lambda x: x[0])
    return round(s, 1), label, why


def bull_score(r):
    return _best(bull_setups(r))


def bear_score(r):
    return _best(bear_setups(r))


def shortlist(rows, n=SHORTLIST_N):
    """Best `n` buy and `n` sell candidates on price setup alone.

    Names reporting earnings inside the hold window are left out of both lists: an
    earnings print is a coin flip the setup says nothing about. A name that qualifies on
    both sides goes to the side where it scores higher."""
    buys, sells = [], []
    for r in rows:
        if r.get("earnSoon"):
            continue
        bs, bl, bw = bull_score(r)
        ss, sl, sw = bear_score(r)
        if bs >= ss and bs > 0:
            buys.append({"row": r, "side": "long", "setupScore": bs, "setup": bl, "why": bw})
        elif ss > 0:
            sells.append({"row": r, "side": "short", "setupScore": ss, "setup": sl, "why": sw})
    buys.sort(key=lambda c: -c["setupScore"])
    sells.sort(key=lambda c: -c["setupScore"])
    return buys[:n], sells[:n]


def news_points(item, side, source):
    """-20..+20 from one ticker's news read; mirrored for sells."""
    if not item:
        return 0.0
    pts = item.get("sentiment", 0) * item.get("materiality", 0) * (NEWS_MAX / 4)
    if source != "claude":
        pts *= KEYWORD_WEIGHT
    return round(pts if side == "long" else -pts, 1) + 0.0   # + 0.0 turns -0.0 into 0.0


def sector_tilt(sector, macro, ai_macro):
    """-1..+1: the sector's 20-day strength vs SPY, averaged with Claude's view when
    there is one."""
    rule = ((macro or {}).get("sectors") or {}).get(sector, {}).get("tilt")
    ai = ((ai_macro or {}).get("sectors") or {}).get(sector, {}).get("tilt")
    if rule is None and ai is None:
        return 0.0
    if ai is None:
        return rule
    if rule is None:
        return ai / 2
    return round(0.5 * rule + 0.5 * ai / 2, 2)


def macro_points(r, side, macro, ai_macro):
    """-15..+15: sector tilt (up to 8) plus the risk backdrop (up to 7).

    The risk part scales with the stock's own volatility: a risk-off tape hurts a
    jumpy stock more than a dull one, and a risk-on tape helps it more."""
    tilt = sector_tilt(r.get("sector"), macro, ai_macro)
    risk = ((macro or {}).get("riskScore") or 0) / 100
    vol_f = clamp((r.get("vol20") or 30) / 30, 0.5, 1.5)
    pts = SECTOR_MAX * tilt + clamp(REGIME_MAX * risk * vol_f, -REGIME_MAX, REGIME_MAX)
    return round(pts if side == "long" else -pts, 1) + 0.0


def _levels(r, side):
    atr = r.get("atr") or 0
    if side == "short":
        return round(r["entry"] + STOP_MULT * atr, 2), round(r["entry"] - TARGET_MULT * atr, 2)
    return round(r["entry"] - STOP_MULT * atr, 2), round(r["entry"] + TARGET_MULT * atr, 2)


def rank(cands, macro, read, news_by_sym, sell_by=None, top_n=TOP_N):
    """Score one side's shortlist and return (top picks, vetoed).

    read: the dict from ai_read.read(). A pick is vetoed when the news says not to take
    that side at all (e.g. don't short a takeover target)."""
    source = (read or {}).get("source")
    ai_macro = (read or {}).get("macro")
    tickers = (read or {}).get("tickers") or {}
    scored, vetoed = [], []
    for c in cands:
        r, side = c["row"], c["side"]
        item = tickers.get(r["sym"])
        if item and (item.get("avoidLong") if side == "long" else item.get("avoidShort")):
            vetoed.append({"sym": r["sym"], "side": side, "reason": item.get("reason") or ""})
            continue
        n_pts = news_points(item, side, source)
        m_pts = macro_points(r, side, macro, ai_macro)
        stop, target = _levels(r, side)
        scored.append({
            "sym": r["sym"], "name": r.get("name"), "sector": r.get("sector"),
            "side": side, "setup": c["setup"], "why": c["why"],
            "score": round(c["setupScore"] + n_pts + m_pts, 1),
            "parts": {"setup": c["setupScore"], "news": n_pts, "macro": m_pts},
            "newsReason": (item or {}).get("reason") or "",
            "catalyst": (item or {}).get("catalyst") or "",
            "sentiment": (item or {}).get("sentiment", 0),
            "materiality": (item or {}).get("materiality", 0),
            "headlines": (news_by_sym or {}).get(r["sym"], [])[:3],
            "px": r["px"], "entry": r["entry"], "stop": stop, "target": target,
            "atrPct": r.get("atrPct"), "ret5": r["ret5"], "ret20": r["ret20"],
            "rsi": r["rsi"], "earnDate": r.get("earnDate"), "earnDays": r.get("earnDays"),
            "sellBy": sell_by,
        })
    scored.sort(key=lambda p: (-p["score"], p["sym"]))
    out, per_sector = [], {}
    for p in scored:
        if p["score"] < MIN_SCORE:
            break
        n = per_sector.get(p["sector"], 0)
        if n >= MAX_PER_SECTOR:
            continue
        per_sector[p["sector"]] = n + 1
        out.append(p)
        if len(out) >= top_n:
            break
    for i, p in enumerate(out, 1):
        p["rank"] = i
    return out, vetoed


def candidates_for_read(buys, sells, news_by_sym):
    """The shortlist in the shape ai_read.read() wants."""
    out = []
    for c in buys + sells:
        r = c["row"]
        out.append({"sym": r["sym"], "name": r.get("name"), "sector": r.get("sector"),
                    "side": c["side"], "setup": c["setup"], "ret5": r["ret5"],
                    "ret20": r["ret20"], "headlines": (news_by_sym or {}).get(r["sym"], [])})
    return out


def strategy_entries(top_buys, top_sells, sell_by, stats=None):
    """The two lists as entries for data.js `strategies`, so the paper log, the health
    check, the morning brief and push_orders.py treat them like any other basket."""
    stats = stats or {}
    common = {"holdDays": HOLD_DAYS, "stopMult": STOP_MULT, "targetMult": TARGET_MULT,
              "sellBy": sell_by, "regimeGated": False, "lead": True}
    return [
        {**common, "key": "topBuys", "label": "Top Buys (setup + news + macro)",
         "desc": "The lead buy list: best short-term price setups, re-ranked by what the "
                 "news and the macro backdrop add. The news and macro overlays have no "
                 "backtest - judge them on the paper record as it builds.",
         "picks": [p["sym"] for p in top_buys], "stats": stats.get("topBuys")},
        {**common, "key": "topSells", "label": "Top Sells (setup + news + macro)",
         "side": "short",
         "desc": "The lead sell list: weakest setups, re-ranked by news and macro. Use it "
                 "to avoid, trim or hedge. Every short rule tested here lost money over "
                 "ten years, so shorting these is blocked in the order pusher.",
         "emptyNote": "Nothing scores high enough to flag as a sell right now.",
         "picks": [p["sym"] for p in top_sells], "stats": stats.get("topSells")},
    ]


def build(rows, macro, news_fn, read_fn, last_session, sell_by):
    """Whole pipeline: shortlist -> headlines -> read -> ranked lists.

    news_fn(symbols) -> (by_sym, market, meta); read_fn(macro, market, candidates,
    session) -> read dict. Both are injected so this stays testable offline."""
    buys, sells = shortlist(rows)
    syms = [c["row"]["sym"] for c in buys + sells]
    by_sym, market, news_meta = news_fn(syms)
    read = read_fn(macro, market, candidates_for_read(buys, sells, by_sym), last_session)
    top_buys, veto_b = rank(buys, macro, read, by_sym, sell_by)
    top_sells, veto_s = rank(sells, macro, read, by_sym, sell_by)
    news_meta = dict(news_meta or {}, source=read.get("source"), model=read.get("model"),
                     note=read.get("note"), shortlisted=len(syms),
                     vetoed=veto_b + veto_s)
    return {"topBuys": top_buys, "topSells": top_sells, "newsMeta": news_meta,
            "aiMacro": read.get("macro"), "marketNews": market[:12]}
