"""Top Buys / Top Sells: scoring, exclusions, the news and macro overlays, and the
no-key fallback. All offline - news and the Claude read are injected as plain functions."""

import pytest

import ai_read
import news
import picks
import push_orders as po
import screener


def row(sym, **kw):
    """A neutral row: no setup on either side until fields are overridden."""
    r = {"sym": sym, "name": sym + " Inc", "sector": "Tech/Comms", "px": 100.0,
         "entry": 100.0, "atr": 2.0, "atrPct": 2.0, "vol20": 30.0, "ret5": 0.5,
         "ret20": 1.0, "ret60": 2.0, "rsi": 45.0, "rsi3": 50.0, "dd52": -8.0,
         "px200": 104.0, "volRatio": 1.0, "distHi20": -5.0, "aboveSma20": False,
         "aboveSma50": True, "trendUp": True, "earnSoon": False, "earnDate": None,
         "earnDays": None, "stop": 97.0, "target": 104.0}
    r.update(kw)
    return r


def dip(sym, **kw):       # uptrend, sold off this week
    return row(sym, **{"ret5": -5.0, "rsi3": 15.0, **kw})


def breakdown(sym, **kw):  # below both averages, trend down, still falling
    return row(sym, **{"aboveSma20": False, "aboveSma50": False, "trendUp": False,
                       "ret20": -12.0, "ret5": -3.0, "px200": 92.0, "rsi": 38.0, **kw})


MACRO = {"riskScore": 0, "sectors": {}}


def no_news(symbols):
    return {s: [] for s in symbols}, [], {"tickers": len(symbols)}


def neutral_read(macro, market, candidates, session=None):
    return ai_read.keyword_read(candidates, "test")


# ---- setup scores ---------------------------------------------------------------------

def test_neutral_row_has_no_setup():
    assert picks.bull_score(row("AAA"))[0] == 0
    assert picks.bear_score(row("AAA"))[0] == 0


def test_scores_stay_in_range_and_name_a_setup():
    for r in (dip("A"), breakdown("B"),
              row("C", px200=80.0, aboveSma20=True, dd52=-45.0),
              row("D", rsi=85.0, px200=140.0, ret5=9.0),
              row("E", dd52=-60.0, rsi=30.0)):
        for score, label, why in (picks.bull_score(r), picks.bear_score(r)):
            assert 0 <= score <= 100
            assert (label is None) == (score == 0)
            assert bool(why) == (score > 0)


def test_deeper_dip_scores_higher():
    assert picks.bull_score(dip("A", ret5=-6.0))[0] > picks.bull_score(dip("A", ret5=-2.0))[0]


def test_dip_is_measured_in_atrs_not_percent():
    calm = picks.bull_score(dip("A", ret5=-3.0, atrPct=1.0))[0]
    wild = picks.bull_score(dip("A", ret5=-3.0, atrPct=4.0))[0]
    assert calm > wild


def test_momentum_is_capped_below_the_mean_reversion_setups():
    mom = row("A", aboveSma20=True, rsi=60.0, distHi20=-1.0, ret20=30.0, ret60=60.0,
              volRatio=1.5)
    score, label, _ = picks.bull_score(mom)
    assert label == "Momentum" and score <= 65
    assert picks.bull_score(dip("B"))[0] > score


def test_oversold_breakdown_is_marked_down():
    assert picks.bear_score(breakdown("A", rsi=25.0))[0] < picks.bear_score(breakdown("A"))[0]


# ---- shortlist ------------------------------------------------------------------------

def test_earnings_inside_the_hold_window_are_excluded_from_both_sides():
    buys, sells = picks.shortlist([dip("A", earnSoon=True), breakdown("B", earnSoon=True),
                                   dip("C")])
    assert [c["row"]["sym"] for c in buys] == ["C"]
    assert sells == []


def test_a_name_is_never_on_both_lists():
    rows = [dip("A"), breakdown("B"), row("C", dd52=-60.0, aboveSma20=False,
                                          aboveSma50=False, trendUp=False, ret20=-10.0)]
    buys, sells = picks.shortlist(rows)
    assert not ({c["row"]["sym"] for c in buys} & {c["row"]["sym"] for c in sells})


def test_shortlist_is_capped_and_sorted():
    rows = [dip("S%02d" % i, ret5=-1.0 - i * 0.1) for i in range(40)]
    buys, _ = picks.shortlist(rows, n=25)
    assert len(buys) == 25
    assert [c["setupScore"] for c in buys] == sorted((c["setupScore"] for c in buys),
                                                     reverse=True)


# ---- news overlay ---------------------------------------------------------------------

@pytest.mark.parametrize("sent,mat,side,source,expected", [
    (2, 2, "long", "claude", 20.0),
    (-2, 2, "long", "claude", -20.0),
    (-2, 2, "short", "claude", 20.0),     # bad news helps a sell
    (2, 2, "short", "claude", -20.0),
    (2, 0, "long", "claude", 0.0),        # immaterial news moves nothing
    (2, 2, "long", "keyword", 10.0),      # the crude fallback counts half
])
def test_news_points(sent, mat, side, source, expected):
    item = {"sentiment": sent, "materiality": mat}
    assert picks.news_points(item, side, source) == expected


def test_no_read_means_no_news_points():
    assert picks.news_points(None, "long", "claude") == 0


def test_news_reorders_the_list():
    buys, _ = picks.shortlist([dip("AAA", ret5=-5.0), dip("BBB", ret5=-4.0)])
    read = {"source": "claude", "macro": None, "tickers": {
        "AAA": {"sentiment": -2, "materiality": 2, "reason": "guidance cut"},
        "BBB": {"sentiment": 2, "materiality": 2, "reason": "raised guidance"}}}
    out, _ = picks.rank(buys, MACRO, read, {})
    assert [p["sym"] for p in out] == ["BBB", "AAA"]
    assert out[0]["parts"]["news"] == 20.0 and out[1]["parts"]["news"] == -20.0
    assert out[0]["score"] == pytest.approx(sum(out[0]["parts"].values()))


def test_news_veto_removes_the_pick_from_that_side_only():
    buys, sells = picks.shortlist([dip("AAA"), breakdown("BBB")])
    read = {"source": "claude", "macro": None, "tickers": {
        "AAA": {"sentiment": -2, "materiality": 2, "avoidLong": True, "reason": "fraud probe"},
        "BBB": {"sentiment": 2, "materiality": 2, "avoidShort": True, "reason": "takeover bid"}}}
    top_b, veto_b = picks.rank(buys, MACRO, read, {})
    top_s, veto_s = picks.rank(sells, MACRO, read, {})
    assert top_b == [] and top_s == []
    assert [v["sym"] for v in veto_b] == ["AAA"] and [v["sym"] for v in veto_s] == ["BBB"]


# ---- macro overlay --------------------------------------------------------------------

def test_risk_off_cuts_buys_and_lifts_sells():
    r = dip("AAA")
    on, off = {"riskScore": 80, "sectors": {}}, {"riskScore": -80, "sectors": {}}
    assert picks.macro_points(r, "long", on, None) > 0 > picks.macro_points(r, "long", off, None)
    assert picks.macro_points(r, "short", off, None) > 0 > picks.macro_points(r, "short", on, None)


def test_risk_backdrop_bites_harder_on_volatile_names():
    off = {"riskScore": -50, "sectors": {}}
    calm = picks.macro_points(row("A", vol20=15.0), "long", off, None)
    wild = picks.macro_points(row("A", vol20=45.0), "long", off, None)
    assert wild < calm < 0


def test_sector_tailwind_helps_buys_and_hurts_sells():
    m = {"riskScore": 0, "sectors": {"Tech/Comms": {"tilt": 1.0}}}
    assert picks.macro_points(row("A"), "long", m, None) == picks.SECTOR_MAX
    assert picks.macro_points(row("A"), "short", m, None) == -picks.SECTOR_MAX


def test_claude_sector_view_is_averaged_with_the_price_based_one():
    m = {"sectors": {"Energy": {"tilt": 1.0}}}
    ai = {"sectors": {"Energy": {"tilt": -2}}}
    assert picks.sector_tilt("Energy", m, ai) == 0.0
    assert picks.sector_tilt("Energy", m, None) == 1.0
    assert picks.sector_tilt("Energy", {}, None) == 0.0


def test_macro_points_are_bounded():
    m = {"riskScore": 100, "sectors": {"Tech/Comms": {"tilt": 1.0}}}
    ai = {"sectors": {"Tech/Comms": {"tilt": 2}}}
    assert picks.macro_points(row("A", vol20=90.0), "long", m, ai) == 15.0


# ---- ranking --------------------------------------------------------------------------

def test_sector_cap_and_top_n():
    rows = [dip("T%d" % i, ret5=-5.0 + i * 0.1) for i in range(5)]
    rows += [dip("F%d" % i, sector="Financials", ret5=-4.0 + i * 0.1) for i in range(5)]
    buys, _ = picks.shortlist(rows)
    out, _ = picks.rank(buys, MACRO, None, {})
    assert len(out) == 4                       # two sectors x MAX_PER_SECTOR
    assert [p["sym"] for p in out] == ["T0", "T1", "F0", "F1"]
    assert [p["rank"] for p in out] == [1, 2, 3, 4]


def test_weak_scores_are_not_padded_into_the_list():
    weak = row("AAA", aboveSma20=True, rsi=60.0, distHi20=-1.0)     # momentum, ~35-40
    buys, _ = picks.shortlist([weak])
    assert buys and buys[0]["setupScore"] < picks.MIN_SCORE
    assert picks.rank(buys, MACRO, None, {})[0] == []


def test_pick_levels_match_the_order_pusher():
    """The list, the brief and push_orders.py must show the same stop and target."""
    buys, sells = picks.shortlist([dip("AAA", entry=123.45, atr=3.21), breakdown("BBB")])
    for cands in (buys, sells):
        p = picks.rank(cands, MACRO, None, {})[0][0]
        st = next(s for s in picks.strategy_entries([], [], "2026-01-09")
                  if (s.get("side") == "short") == (p["side"] == "short"))
        assert (p["stop"], p["target"]) == po.levels(cands[0]["row"], st)


def test_strategy_entries_plug_into_the_existing_plumbing():
    b, s = picks.strategy_entries([{"sym": "AAA"}], [{"sym": "BBB"}], "2026-01-09")
    assert (b["key"], b["picks"], b.get("side")) == ("topBuys", ["AAA"], None)
    assert (s["key"], s["picks"], s["side"]) == ("topSells", ["BBB"], "short")
    assert b["holdDays"] == 5 and b["sellBy"] == "2026-01-09"
    assert "topBuys" in po.DEFAULTS["strategies"]
    assert "topSells" in po.DEFAULTS["never_trade"]


def test_build_runs_the_whole_pipeline_offline():
    rows = [dip("AAA"), dip("BBB", sector="Financials"), breakdown("CCC")]
    out = picks.build(rows, MACRO, no_news, neutral_read, "2026-01-02", "2026-01-09")
    assert [p["sym"] for p in out["topBuys"]] == ["AAA", "BBB"]
    assert [p["sym"] for p in out["topSells"]] == ["CCC"]
    assert out["newsMeta"]["source"] == "keyword"
    assert out["topBuys"][0]["sellBy"] == "2026-01-09"


# ---- news fetch hygiene ---------------------------------------------------------------

def test_clean_keeps_only_recent_tagged_stories():
    now = 1_800_000_000
    items = [
        {"uuid": "a", "title": "Fresh and tagged", "providerPublishTime": now - 3600,
         "publisher": "X", "link": "https://example.com/a", "relatedTickers": ["AAA"]},
        {"uuid": "b", "title": "Too old", "providerPublishTime": now - 9 * 86400,
         "relatedTickers": ["AAA"]},
        {"uuid": "c", "title": "About another ticker", "providerPublishTime": now - 60,
         "relatedTickers": ["ZZZ"]},
        {"uuid": "a", "title": "Duplicate", "providerPublishTime": now - 10,
         "relatedTickers": ["AAA"]},
        {"uuid": "d", "title": "Odd link", "providerPublishTime": now - 120,
         "link": "javascript:alert(1)", "relatedTickers": ["AAA"]},
    ]
    out = news.clean(items, now, ticker="AAA")
    assert [h["id"] for h in out] == ["d", "a"]         # newest first
    assert out[0]["u"] == ""                            # only https links survive
    assert out[1]["u"] == "https://example.com/a"


# ---- Claude read: fallback and sanitising --------------------------------------------

def test_no_key_falls_back_to_keywords_and_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_read, "KEY_FILE", str(tmp_path / "missing.txt"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    cands = [{"sym": "AAA", "side": "long", "headlines": [{"id": "1", "t": "AAA beats estimates and raises guidance", "ts": 1}]},
             {"sym": "BBB", "side": "long", "headlines": []}]
    out = ai_read.read(MACRO, [], cands)
    assert out["source"] == "keyword" and "no API key" in out["note"]
    assert out["macro"] is None
    assert out["tickers"]["AAA"]["sentiment"] > 0
    assert out["tickers"]["BBB"]["sentiment"] == 0


def test_api_failure_falls_back_instead_of_raising(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_read, "KEY_FILE", str(tmp_path / "k.txt"))
    (tmp_path / "k.txt").write_text("sk-test\n")
    monkeypatch.setattr(ai_read, "CACHE", str(tmp_path / "cache.json"))

    def boom(prompt, key):
        raise RuntimeError("network down")
    monkeypatch.setattr(ai_read, "call_claude", boom)
    out = ai_read.read(MACRO, [], [{"sym": "AAA", "side": "long", "headlines": []}])
    assert out["source"] == "keyword" and "network down" in out["note"]


def test_claude_read_is_cached_while_headlines_are_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_read, "KEY_FILE", str(tmp_path / "k.txt"))
    (tmp_path / "k.txt").write_text("sk-test\n")
    monkeypatch.setattr(ai_read, "CACHE", str(tmp_path / "cache.json"))
    calls = []

    class Usage:
        input_tokens, output_tokens = 10, 5

    def fake(prompt, key):
        calls.append(prompt)
        return {"macro": {"summary": "ok", "stance": "mixed", "risks": [], "sectors": []},
                "tickers": [{"sym": "AAA", "sentiment": 1, "materiality": 2,
                             "reason": "r", "catalyst": "c", "avoid_long": False,
                             "avoid_short": False}]}, Usage()
    monkeypatch.setattr(ai_read, "call_claude", fake)
    cands = [{"sym": "AAA", "side": "long", "headlines": [{"id": "h1", "t": "x", "p": "P", "ts": 1}]}]
    a = ai_read.read(MACRO, [], cands, "2026-01-02")
    b = ai_read.read(MACRO, [], cands, "2026-01-02")
    assert a["source"] == b["source"] == "claude" and len(calls) == 1
    cands[0]["headlines"].append({"id": "h2", "t": "y", "p": "P", "ts": 2})
    ai_read.read(MACRO, [], cands, "2026-01-02")
    assert len(calls) == 2                       # new headline -> new read


def test_sanitize_clamps_and_ignores_tickers_we_did_not_ask_about():
    raw = {"macro": {"summary": "s", "stance": "sell everything", "risks": ["r"] * 20,
                     "sectors": [{"sector": "Energy", "tilt": 9, "reason": "x"},
                                 {"sector": "Crypto", "tilt": 2, "reason": "x"}]},
           "tickers": [{"sym": "AAA", "sentiment": 99, "materiality": -4, "reason": "x" * 999,
                        "catalyst": "c", "avoid_long": "yes", "avoid_short": True},
                       {"sym": "EVIL", "sentiment": 2, "materiality": 2}]}
    macro, tickers = ai_read.sanitize(raw, [{"sym": "AAA"}])
    assert list(tickers) == ["AAA"]
    t = tickers["AAA"]
    assert (t["sentiment"], t["materiality"]) == (2, 0)
    assert len(t["reason"]) == 300
    assert t["avoidLong"] is False and t["avoidShort"] is True      # only a real True counts
    assert macro["stance"] == "mixed" and len(macro["risks"]) == 6
    assert macro["sectors"] == {"Energy": {"tilt": 2, "reason": "x"}}


def test_prompt_contains_the_data_and_every_shortlisted_name():
    macro = {"riskScore": -3, "riskLabel": "Mixed", "breadth20": 60.0, "credit20": -1.5,
             "curve10y3m": 0.8, "sectors": {"Energy": {"rel20": -3.8, "tilt": -0.6}},
             "tiles": [{"label": "VIX", "last": 19.5, "chg5": -1.4, "chg20": 4.2, "kind": "pts"}]}
    cands = [{"sym": "AAA", "name": "A", "sector": "Energy", "side": "short",
              "setup": "Breakdown", "ret5": -3.0, "ret20": -12.0,
              "headlines": [{"id": "1", "t": "AAA cuts guidance", "p": "Wire", "ts": 1}]},
             {"sym": "BBB", "side": "long", "headlines": []}]
    p = ai_read.build_prompt(macro, [{"id": "m", "t": "Fed holds", "p": "Wire", "ts": 1}], cands)
    for needle in ("VIX: 19.5", "Fed holds", "AAA cuts guidance", "possible SELL",
                   "BBB", "(no recent headlines)", "<shortlist>"):
        assert needle in p


def test_schema_is_closed_everywhere():
    """Structured outputs require additionalProperties:false on every object."""
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node["required"]) == set(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(ai_read.SCHEMA)


# ---- half-formed intraday bar ---------------------------------------------------------

def test_partial_bar_is_dropped_only_while_the_session_is_open():
    meta = {"currentTradingPeriod": {"regular": {"start": 1000, "end": 2000}}}
    done, live = {"t": 500, "c": 1}, {"t": 1200, "c": 2}
    assert screener.drop_partial_bar([done, live], meta, now=1300) == [done]
    assert screener.drop_partial_bar([done, live], meta, now=2500) == [done, live]   # closed
    assert screener.drop_partial_bar([done], meta, now=1300) == [done]               # no live bar yet
    assert screener.drop_partial_bar([done, live], {}, now=1300) == [done, live]     # no session info
