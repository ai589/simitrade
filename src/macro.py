# Macro backdrop for the Top Buys / Top Sells lists.
#
# Pulls a handful of cross-asset series from the same Yahoo chart endpoint the screener
# uses (no key), and boils them down to:
#   - tiles:   level + 5d / 20d change for VIX, 10y yield, dollar, credit, oil, gold...
#   - risk:    a rule-based score from -100 (risk-off) to +100 (risk-on), with every
#              component and its points listed so the number is never a black box
#   - sectors: 20-day relative strength of each of the 7 sector buckets vs SPY
#
# Everything below the fetch is a pure function over lists of closes, so it is tested
# offline (tests/test_macro.py). This module must not import screener (screener imports
# it); the fetch function is passed in.

import concurrent.futures

# label, how a change is expressed: "pct" (percent move) or "pts" (absolute move)
SERIES = {
    "^VIX":     ("VIX", "pts"),
    "^VIX3M":   ("VIX 3-month", "pts"),
    "^TNX":     ("US 10y yield", "pts"),
    "^IRX":     ("US 3m yield", "pts"),
    "DX-Y.NYB": ("Dollar index", "pct"),
    "HYG":      ("High-yield credit (HYG)", "pct"),
    "IEF":      ("7-10y Treasuries (IEF)", "pct"),
    "CL=F":     ("Crude oil (WTI)", "pct"),
    "GC=F":     ("Gold", "pct"),
    "SPY":      ("S&P 500 (SPY)", "pct"),
    "QQQ":      ("Nasdaq-100 (QQQ)", "pct"),
    "IWM":      ("Small caps (IWM)", "pct"),
}

# sector ETF -> one of universe.BUCKETS
SECTOR_ETFS = {
    "XLF": "Financials", "XLV": "Healthcare", "XLE": "Energy", "XLI": "Industrials",
    "XLK": "Tech/Comms", "XLC": "Tech/Comms",
    "XLY": "Consumer", "XLP": "Consumer",
    "XLB": "Materials/Util/REIT", "XLU": "Materials/Util/REIT", "XLRE": "Materials/Util/REIT",
}

TILE_ORDER = ["SPY", "QQQ", "IWM", "^VIX", "^TNX", "DX-Y.NYB", "HYG", "CL=F", "GC=F"]


def clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def series_stats(closes, kind="pct"):
    """Level and 5d / 20d change of one series. Changes are percent for prices and
    absolute points for things already quoted in points (VIX, yields)."""
    closes = [c for c in closes if c is not None]
    if len(closes) < 21:
        return None

    def chg(n):
        a, b = closes[-1], closes[-1 - n]
        if kind == "pts":
            return round(a - b, 2)
        return round((a / b - 1) * 100, 2) if b else None

    out = {"last": round(closes[-1], 2), "chg5": chg(5), "chg20": chg(20), "kind": kind}
    if len(closes) >= 50:
        out["sma50"] = round(sum(closes[-50:]) / 50, 2)
    if len(closes) >= 200:
        out["sma200"] = round(sum(closes[-200:]) / 200, 2)
    return out


def ratio_change(a, b, n=20):
    """Percent change over n sessions of the ratio a/b (e.g. HYG/IEF: credit vs
    Treasuries - falling means credit is being sold relative to safe bonds)."""
    a = [x for x in a if x is not None]
    b = [x for x in b if x is not None]
    m = min(len(a), len(b))
    if m < n + 1:
        return None
    a, b = a[-m:], b[-m:]
    now, then = a[-1] / b[-1], a[-1 - n] / b[-1 - n]
    return round((now / then - 1) * 100, 2)


def risk_score(stats, breadth20=None, credit20=None):
    """Rule-based risk appetite, -100 (risk-off) .. +100 (risk-on).

    Returns (score, label, components). Each component is {name, read, pts, max} so the
    dashboard can show exactly where the number came from. Missing inputs score 0."""
    comps = []

    def add(name, read, pts, mx):
        comps.append({"name": name, "read": read, "pts": round(pts, 1), "max": mx})

    spy = stats.get("SPY") or {}
    if spy.get("sma50"):
        up = spy["last"] > spy["sma50"]
        add("S&P 500 vs 50-day", "above" if up else "below", 20 if up else -20, 20)
    if spy.get("sma200"):
        up = spy["last"] > spy["sma200"]
        add("S&P 500 vs 200-day", "above" if up else "below", 15 if up else -15, 15)
    if breadth20 is not None:
        add("Breadth (% above 20-day)", "%.0f%%" % breadth20,
            clamp((breadth20 - 50) / 30) * 15, 15)
    vix = stats.get("^VIX") or {}
    if vix.get("last") is not None:
        add("VIX level", "%.1f" % vix["last"], clamp((20 - vix["last"]) / 10) * 15, 15)
        v3 = (stats.get("^VIX3M") or {}).get("last")
        if v3:
            r = vix["last"] / v3
            pts = -10 if r > 1.0 else (5 if r < 0.9 else 0)
            add("VIX term structure", "%.2f (>1 = stress)" % r, pts, 10)
    if credit20 is not None:
        add("Credit vs Treasuries, 20d", "%+.1f%%" % credit20, clamp(credit20 / 2.0) * 15, 15)
    tnx = stats.get("^TNX") or {}
    if tnx.get("chg20") is not None:
        add("10y yield, 20d change", "%+.2f pts" % tnx["chg20"],
            clamp(-tnx["chg20"] / 0.4) * 5, 5)
    dxy = stats.get("DX-Y.NYB") or {}
    if dxy.get("chg20") is not None:
        add("Dollar, 20d change", "%+.1f%%" % dxy["chg20"], clamp(-dxy["chg20"] / 3.0) * 5, 5)

    score = round(clamp(sum(c["pts"] for c in comps), -100, 100))
    label = "Risk-on" if score >= 25 else "Risk-off" if score <= -25 else "Mixed"
    return score, label, comps


def sector_strength(etf_ret20, spy_ret20):
    """20-day return of each sector bucket minus SPY's, and a -1..+1 rule tilt.

    etf_ret20: {etf: 20d % return}. A bucket with several ETFs takes their average."""
    by_bucket = {}
    for etf, bucket in SECTOR_ETFS.items():
        r = etf_ret20.get(etf)
        if r is not None:
            by_bucket.setdefault(bucket, []).append(r)
    out = {}
    for bucket, rets in by_bucket.items():
        rel = sum(rets) / len(rets) - (spy_ret20 or 0)
        out[bucket] = {"rel20": round(rel, 2), "tilt": round(clamp(rel / 6.0), 2)}
    return out


def snapshot(fetch, breadth20=None):
    """Fetch everything and return the macro block written to data.js.

    `fetch` is screener.fetch: sym -> {"rows": [{"c": ...}, ...]} or None. A series that
    fails to load is simply left out; the score is built from whatever arrived."""
    syms = list(SERIES) + list(SECTOR_ETFS)
    closes = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for sym, d in zip(syms, ex.map(fetch, syms)):
            if d and d.get("rows"):
                closes[sym] = [r["c"] for r in d["rows"]]

    stats = {}
    for sym, (label, kind) in SERIES.items():
        s = series_stats(closes.get(sym, []), kind)
        if s:
            s["label"] = label
            stats[sym] = s

    credit20 = None
    if "HYG" in closes and "IEF" in closes:
        credit20 = ratio_change(closes["HYG"], closes["IEF"], 20)

    score, label, comps = risk_score(stats, breadth20, credit20)

    etf_ret20 = {}
    for etf in SECTOR_ETFS:
        s = series_stats(closes.get(etf, []), "pct")
        if s:
            etf_ret20[etf] = s["chg20"]
    sectors = sector_strength(etf_ret20, (stats.get("SPY") or {}).get("chg20"))

    tiles = [dict(stats[s], sym=s) for s in TILE_ORDER if s in stats]
    curve = None
    if stats.get("^TNX") and stats.get("^IRX"):
        curve = round(stats["^TNX"]["last"] - stats["^IRX"]["last"], 2)

    return {
        "riskScore": score, "riskLabel": label, "components": comps,
        "tiles": tiles, "credit20": credit20, "curve10y3m": curve,
        "sectors": sectors, "etfRet20": etf_ret20,
        "missing": [s for s in syms if s not in closes],
    }
