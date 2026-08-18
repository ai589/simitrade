# Screening universe: S&P 500 + Nasdaq-100 + Dow 30 constituents (Wikipedia) plus a
# short pinned EXTRAS list. Cached weekly in state/universe.json; on any fetch/parse
# failure the cache is used regardless of age (with a WARNING) so unattended refreshes
# never break on a Wikipedia hiccup. Only if there is no cache at all does it abort.
#
# Sectors: each name's GICS sector (S&P table) is folded into the dashboard's 7 buckets;
# fallbacks in order: hand map (for extras) -> Nasdaq-100 table's ICB industry ->
# Yahoo profile sector from state/earnings_cache.json -> "Other".
#
# Usage:  python universe.py [--refresh]      (self-test / manual refresh)

import collections
import datetime
import json
import os
import re
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state")
CACHE = os.path.join(STATE, "universe.json")
EARN_CACHE = os.path.join(STATE, "earnings_cache.json")  # read-only here (sector fallback)
TTL_DAYS = 7
SCHEMA = 2  # bump when the cached info shape changes; older caches are rebuilt

# Wikipedia returns 403 to the default python User-Agent; a descriptive UA is required.
WIKI_HEADERS = {"User-Agent": "ECLScreener/1.0 (ai@bepc.com.sg)"}
WIKI = "https://en.wikipedia.org/wiki/{}?action=raw"
PAGES = {
    "SPX": "List_of_S%26P_500_companies",
    "NDX": "List_of_NASDAQ-100_companies",  # uppercase NASDAQ - the title-case page does not exist
    "DJI": "List_of_Dow_Jones_Industrial_Average_companies",  # components moved here 2026-08-15
}
# Sanity bounds on parsed row counts (S&P has 503 rows incl. dual classes, NDX 102, DJI 30).
FLOOR = {"SPX": 450, "NDX": 90, "DJI": 25}
CEIL = {"SPX": 520, "NDX": 110, "DJI": 35}

BUCKETS = ["Financials", "Healthcare", "Energy", "Industrials",
           "Tech/Comms", "Consumer", "Materials/Util/REIT"]

# One fold map covers GICS (S&P table), ICB (Nasdaq-100 table) and Yahoo assetProfile.sector.
SECTOR7 = {
    "Financials": "Financials", "Financial Services": "Financials",
    "Health Care": "Healthcare", "Healthcare": "Healthcare",
    "Energy": "Energy",
    "Industrials": "Industrials",
    "Information Technology": "Tech/Comms", "Technology": "Tech/Comms",
    "Communication Services": "Tech/Comms", "Telecommunications": "Tech/Comms",
    "Consumer Discretionary": "Consumer", "Consumer Staples": "Consumer",
    "Consumer Cyclical": "Consumer", "Consumer Defensive": "Consumer",
    "Materials": "Materials/Util/REIT", "Basic Materials": "Materials/Util/REIT",
    "Utilities": "Materials/Util/REIT", "Real Estate": "Materials/Util/REIT",
}


def fold_sector(raw):
    """GICS / ICB / Yahoo sector string -> one of BUCKETS, or None if unknown."""
    return SECTOR7.get((raw or "").strip())


# Names kept in the universe even though they are in none of the three indices
# (ADRs and a few growth names carried over from the old hand-curated list).
EXTRAS = ["NVO", "AZN", "NVS", "LNG", "SHEL", "BP", "SNOW", "NET", "TSM", "ZS", "TEAM",
          "EA", "BIDU", "NTES", "SPOT", "SE", "SAP", "TM", "JD", "TCOM", "BABA"]

# Hand sector map carried over from the old curated list (dot-form tickers). Used only
# for names whose GICS sector is not on the S&P table (i.e. the extras).
HAND_SECTORS = {
    "Financials": ["JPM", "BAC", "WFC", "C", "GS", "MS", "SCHW", "AXP", "V", "MA", "BLK",
                   "KKR", "BX", "APO", "USB", "PNC", "TFC", "COF", "BK", "SPGI", "MCO",
                   "ICE", "AON", "AJG", "CB", "PGR", "TRV", "ALL", "MET", "PRU",
                   "AIG", "BRK.B", "AFL", "SYF", "CME", "IBKR"],
    "Healthcare": ["UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT", "BMY", "CVS",
                   "DHR", "SYK", "BSX", "MDT", "EW", "ZTS", "ELV", "CI", "HUM", "HCA",
                   "AMGN", "GILD", "VRTX", "REGN", "ISRG", "IDXX", "DXCM",
                   "MCK", "BDX", "GEHC", "A", "IQV", "RMD", "NVO", "AZN", "NVS"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG", "OXY", "VLO", "PSX", "MPC", "KMI",
               "WMB", "OKE", "HAL", "DVN", "FANG", "BKR", "TRGP", "LNG",
               "SHEL", "BP"],
    "Industrials": ["BA", "CAT", "DE", "GE", "LMT", "RTX", "UNP", "UPS", "FDX", "MMM",
                    "ETN", "EMR", "PH", "ROK", "ITW", "GD", "NOC", "TDG", "CMI", "NSC",
                    "WM", "RSG", "URI", "PWR",
                    "HON", "CSX", "ODFL", "PCAR", "ADP", "PAYX",
                    "GEV", "VRT", "HWM", "AXON"],
    "Tech/Comms": ["CRM", "ORCL", "IBM", "ACN", "NOW", "UBER", "SNOW", "NET", "SHOP",
                   "TSM", "ANET", "DELL", "HPQ", "PLTR", "XYZ", "T", "VZ", "DIS",
                   "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AVGO", "AMD", "ADBE", "QCOM",
                   "INTC", "TXN", "MU", "AMAT", "LRCX", "KLAC", "PANW", "CRWD", "INTU",
                   "CSCO", "NFLX", "CMCSA", "APP", "DASH", "TTD", "ZS", "DDOG", "FTNT",
                   "WDAY", "TEAM", "MRVL", "SMCI", "ARM", "COIN", "HOOD", "MSTR", "PYPL", "EA",
                   "ADI", "NXPI", "SNPS", "CDNS", "ASML", "APH", "MSI", "SPOT", "SE",
                   "BIDU", "NTES", "SAP"],
    "Consumer": ["WMT", "HD", "LOW", "MCD", "NKE", "TGT", "PG", "KO", "CL", "KMB", "MO",
                 "PM", "F", "GM", "CMG", "YUM", "SBUX", "DG",
                 "AMZN", "TSLA", "COST", "PEP", "MDLZ", "BKNG", "ABNB", "MAR", "ORLY",
                 "ROST", "LULU", "MNST", "KDP", "KHC",
                 "TJX", "RCL", "HLT", "EL", "DECK", "TM",
                 "PDD", "JD", "TCOM", "MELI", "BABA"],
    "Materials/Util/REIT": ["LIN", "APD", "SHW", "FCX", "NUE", "DOW", "DD", "ECL", "NEE",
                            "DUK", "SO", "SPG", "PLD", "AMT", "O", "CCI",
                            "EXC", "XEL", "AEP", "SBAC", "EQIX",
                            "VST", "CEG", "NEM", "DLR", "PSA", "SRE", "D"],
}
HAND_SECTOR_OF = {s: k for k, v in HAND_SECTORS.items() for s in v}


class UniverseError(Exception):
    pass


# ---------------------------------------------------------------- wikitext parsing

def _get(page):
    r = requests.get(WIKI.format(page), headers=WIKI_HEADERS, timeout=30)
    r.raise_for_status()
    t = re.sub(r"<!--.*?-->", "", r.text, flags=re.S)
    return re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", t, flags=re.S)


def _table(text, marker='id="constituents"'):
    i = text.find(marker)
    if i < 0:
        raise UniverseError("constituents table not found")
    j = text.find("\n|}", i)
    return text[i:j if j > 0 else len(text)]


def _cells(chunk):
    """One '|-' row -> list of cell strings (handles both '||' and newline-'|' forms)."""
    return [c.strip() for c in re.split(r"\n\|\|?|\|\|", chunk) if c.strip()]


# First argument of any ticker template: {{NyseSymbol|X}}, {{NASDAQ link|X}}, {{BZX link|X}}...
_TPL = re.compile(r"\{\{[^|}]*\|\s*([A-Za-z.\-]{1,7})\s*[|}]")
_LINKED = re.compile(r"\[\[(?:[^|\]]*\|)?([A-Z.\-]{1,7})\]\]")
_BARE = re.compile(r"^([A-Z][A-Z.\-]{0,6})$")
_SYM_OK = re.compile(r"^[A-Z]{1,5}([.\-][A-Z])?$")


def _ticker(cell):
    m = _TPL.search(cell) or _LINKED.match(cell) or _BARE.match(cell)
    if not m:
        raise UniverseError(f"no ticker in cell {cell[:40]!r}")
    return m.group(1).upper()


def _plain(cell):
    """Strip wiki links: [[a|b]] -> b, [[a]] -> a."""
    return re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", cell).strip()


_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def parse_spx(text):
    """S&P 500 table -> {dot_sym: {"name", "gics", "added"}}. Rows are one cell per line:
    Symbol | Security | GICS Sector | GICS Sub-Industry | HQ | Date added | CIK | Founded.
    `added` (ISO date or None) lets the backtests treat a name as eligible only from the
    day it joined the index - a partial fix for survivorship bias."""
    out = {}
    for ch in _table(text).split("\n|-"):
        c = _cells(ch)
        if len(c) < 3 or c[0].startswith("!") or c[0].startswith("{|"):
            continue
        m = _DATE.search(c[5]) if len(c) > 5 else None
        out[_ticker(c[0])] = {"name": _plain(c[1]), "gics": _plain(c[2]),
                              "added": m.group(1) if m else None}
    return out


_NDX = re.compile(r"^\|\s*([A-Z][A-Z.\-]{0,6})\s*\|\|\s*(.*?)\s*\|\|\s*(.*?)\s*\|\|", re.M)


def parse_ndx(text):
    """Nasdaq-100 table -> {dot_sym: {"name", "icb"}}. Rows are single lines, bare ticker."""
    return {t.upper(): {"name": _plain(n), "icb": _plain(s)}
            for t, n, s in _NDX.findall(_table(text))}


_LINK = re.compile(r"\{\{\s*[A-Za-z ]*?(?:link|symbol)\s*\|\s*([A-Za-z.\-]{1,7})\s*[|}]", re.I)


def parse_dji(text):
    """Dow 30 table -> {dot_sym}. Ticker sits in a {{NYSE link|X}} / {{NASDAQ link|X}} cell."""
    return {t.upper() for t in _LINK.findall(_table(text))}


# ---------------------------------------------------------------- assembly

def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _earnings_cache_sectors():
    d = _read_json(EARN_CACHE) or {}
    return {s: (p or {}).get("sector") for s, p in (d.get("profiles") or {}).items()}


def build_universe():
    """Fetch + parse the three lists. Raises UniverseError on anything that looks off."""
    spx = parse_spx(_get(PAGES["SPX"]))
    ndx = parse_ndx(_get(PAGES["NDX"]))
    dji = parse_dji(_get(PAGES["DJI"]))
    for k, n in (("SPX", len(spx)), ("NDX", len(ndx)), ("DJI", len(dji))):
        if not FLOOR[k] <= n <= CEIL[k]:
            raise UniverseError(f"{k} count {n} outside [{FLOOR[k]}, {CEIL[k]}]")
    if len(dji - set(spx)) > 5:
        raise UniverseError("DJI parse suspicious (not a subset of the S&P 500)")

    earn_sec = _earnings_cache_sectors()
    info = {}
    for raw in set(spx) | set(ndx) | dji | set(EXTRAS):
        t = raw.replace("-", ".")
        if not _SYM_OK.match(t):
            raise UniverseError(f"bad ticker {t!r}")
        gics = spx.get(t, {}).get("gics")
        sector7 = (fold_sector(gics) or HAND_SECTOR_OF.get(t)
                   or fold_sector(ndx.get(t, {}).get("icb"))
                   or fold_sector(earn_sec.get(t)) or "Other")
        ix = [k for k, m in (("SPX", spx), ("NDX", ndx), ("DJI", dji)) if t in m]
        info[t] = {"name": (spx.get(t) or ndx.get(t) or {}).get("name"),
                   "gics": gics, "sector7": sector7, "ix": ix or ["XTRA"],
                   "added": spx.get(t, {}).get("added")}
    symbols = sorted(t.replace(".", "-") for t in info)  # Yahoo form, deterministic order
    meta = {"schema": SCHEMA, "ts": time.time(), "date": datetime.date.today().isoformat(),
            "counts": {"spx": len(spx), "ndx": len(ndx), "dji": len(dji),
                       "extras": len(EXTRAS), "total": len(symbols)}}
    return symbols, info, meta


def load_universe(force=False):
    """-> (symbols [Yahoo form, sorted], info {dot_sym: {...}}, meta).
    Weekly cache in state/universe.json; on any fetch/parse/sanity failure falls back to
    the cache (any age) with a WARNING; with no cache at all -> SystemExit, so data.js is
    never overwritten with a bad universe and the publish step is skipped."""
    cache = _read_json(CACHE)
    if cache and not {"symbols", "info", "meta"} <= set(cache):
        cache = None
    if cache and cache["meta"].get("schema", 1) < SCHEMA:
        force = True  # old shape: rebuild now, but keep it as the failure fallback
    if cache and not force and time.time() - cache["meta"].get("ts", 0) < TTL_DAYS * 86400:
        return cache["symbols"], cache["info"], cache["meta"]
    try:
        symbols, info, meta = build_universe()
        os.makedirs(STATE, exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump({"symbols": symbols, "info": info, "meta": meta}, f)
        print(f"Universe refreshed from Wikipedia: {meta['counts']}")
        return symbols, info, meta
    except Exception as e:
        if cache:
            print(f"WARNING: universe refresh failed ({e}); using cached universe "
                  f"from {cache['meta'].get('date')}")
            return cache["symbols"], cache["info"], cache["meta"]
        raise SystemExit(f"ABORT: universe unavailable ({e}) and no {CACHE} cache.")


if __name__ == "__main__":
    syms, info, meta = load_universe(force="--refresh" in sys.argv)
    print(meta)
    print(dict(collections.Counter(i["sector7"] for i in info.values())))
    print("Other:", sorted(s for s, i in info.items() if i["sector7"] == "Other"))
    print("Extras:", sorted(s for s, i in info.items() if i["ix"] == ["XTRA"]))
