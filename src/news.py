# Headlines for the shortlisted names and for the market as a whole.
#
# Source: Yahoo Finance's search endpoint (no key). It returns the latest stories Yahoo
# has tagged to a ticker, from many publishers. Only the headline, publisher, link and
# time are kept - article bodies are never fetched.
#
# Headlines are third-party text. Nothing here acts on them; they are passed to
# ai_read.py as quoted data and shown on the dashboard as plain text links.

import concurrent.futures
import json
import os
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "state", "news_cache.json")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"

CACHE_TTL = 3 * 3600        # a ticker's headlines are refetched after 3 hours
MAX_AGE_DAYS = 5            # older stories are already in the price
PER_TICKER = 6
# Market-wide stories: Yahoo's search returns little for phrases like "Federal Reserve"
# but tags its market, rates, oil and volatility coverage to these symbols.
MARKET_QUERIES = ["^GSPC", "SPY", "^TNX", "TLT", "CL=F", "^VIX", "DX-Y.NYB", "GLD"]
MARKET_MAX = 30
MARKET_MAX_AGE_DAYS = 3


def _search(q, count=10):
    for attempt in range(3):
        try:
            r = requests.get(SEARCH_URL, params={"q": q, "newsCount": count,
                                                 "quotesCount": 0},
                             headers=HEADERS, timeout=15)
            if r.status_code == 429:
                time.sleep(2 + attempt * 3)
                continue
            r.raise_for_status()
            return r.json().get("news") or []
        except Exception:
            if attempt == 2:
                return None
            time.sleep(1 + attempt)
    return None


def clean(items, now=None, max_age_days=MAX_AGE_DAYS, ticker=None, limit=PER_TICKER):
    """Raw Yahoo news items -> [{id, t, p, u, ts}], newest first.

    With `ticker` (Yahoo form, e.g. BRK-B) only stories Yahoo tagged to that ticker are
    kept - the search is fuzzy and otherwise returns stories that merely mention it."""
    now = now or time.time()
    out, seen = [], set()
    for it in items or []:
        title = (it.get("title") or "").strip()
        ts = it.get("providerPublishTime")
        uid = it.get("uuid") or title
        if not title or not ts or uid in seen:
            continue
        if now - ts > max_age_days * 86400 or ts > now + 86400:
            continue
        if ticker and ticker not in (it.get("relatedTickers") or []):
            continue
        seen.add(uid)
        link = it.get("link") or ""
        out.append({"id": uid, "t": title[:220], "p": (it.get("publisher") or "")[:60],
                    "u": link if link.startswith("https://") else "", "ts": int(ts)})
    out.sort(key=lambda x: -x["ts"])
    return out[:limit]


def _load_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache):
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except OSError:
        pass


def fetch_news(symbols, now=None):
    """Headlines per symbol (dot-form in, dot-form out) plus market-wide headlines.

    Returns ({sym: [items]}, [market items], meta). A symbol whose fetch fails keeps its
    cached headlines if it has any; meta["failed"] counts the ones that have nothing."""
    now = now or time.time()
    cache = _load_cache()
    tick = cache.setdefault("tickers", {})

    def one(sym):
        c = tick.get(sym)
        if c and now - c.get("fetched", 0) < CACHE_TTL:
            return sym, c["items"], True
        raw = _search(sym.replace(".", "-"), 12)
        if raw is None:
            return sym, (c or {}).get("items"), False
        return sym, clean(raw, now, ticker=sym.replace(".", "-")), False

    by_sym, failed, fetched = {}, 0, 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        for sym, items, cached in ex.map(one, symbols):
            if items is None:
                failed += 1
                items = []
            elif not cached:
                fetched += 1
                tick[sym] = {"fetched": now, "items": items}
            # re-apply the age cut-off: a cached story may have aged out since
            by_sym[sym] = [i for i in items if now - i["ts"] <= MAX_AGE_DAYS * 86400]

    mk = cache.get("market")
    if mk and now - mk.get("fetched", 0) < CACHE_TTL:
        market = mk["items"]
    else:
        raw = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            for res in ex.map(lambda q: _search(q, 10), MARKET_QUERIES):
                raw.extend(res or [])
        market = clean(raw, now, max_age_days=MARKET_MAX_AGE_DAYS, limit=MARKET_MAX)
        if market:
            cache["market"] = {"fetched": now, "items": market}
        elif mk:
            market = mk["items"]

    # drop cache entries for names no longer shortlisted and older than a week
    for s in [s for s, c in tick.items() if now - c.get("fetched", 0) > 7 * 86400]:
        del tick[s]
    _save_cache(cache)

    meta = {"tickers": len(symbols), "fetched": fetched, "failed": failed,
            "withNews": sum(1 for v in by_sym.values() if v), "market": len(market)}
    return by_sym, market, meta
