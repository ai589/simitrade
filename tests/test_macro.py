"""The macro snapshot: series maths, the risk score, sector strength. Offline - the
fetch function is a stub."""

import pytest

import macro


def ramp(start, step, n=260):
    return [start + step * i for i in range(n)]


def test_series_stats_percent_and_points():
    s = macro.series_stats([100.0] * 240 + [110.0] * 20 + [121.0], "pct")
    assert s["last"] == 121.0
    assert s["chg5"] == pytest.approx(10.0)
    assert s["chg20"] == pytest.approx(10.0)
    assert "sma50" in s and "sma200" in s
    v = macro.series_stats([15.0] * 30 + [22.0], "pts")
    assert v["chg5"] == 7.0 and v["kind"] == "pts"


def test_series_stats_needs_history_and_skips_gaps():
    assert macro.series_stats([1.0] * 10) is None
    assert macro.series_stats([None] + [1.0] * 25)["last"] == 1.0


def test_ratio_change():
    hyg = [100.0] * 30 + [97.0]
    ief = [100.0] * 31
    assert macro.ratio_change(hyg, ief, 20) == pytest.approx(-3.0)
    assert macro.ratio_change([1.0] * 5, [1.0] * 5, 20) is None


def stats(spy_last=110, sma50=100, sma200=90, vix=14, vix3m=17, tnx20=0.0, dxy20=0.0):
    return {"SPY": {"last": spy_last, "sma50": sma50, "sma200": sma200, "chg20": 2.0},
            "^VIX": {"last": vix}, "^VIX3M": {"last": vix3m},
            "^TNX": {"chg20": tnx20}, "DX-Y.NYB": {"chg20": dxy20}}


def test_calm_uptrend_reads_risk_on():
    score, label, comps = macro.risk_score(stats(), breadth20=75, credit20=1.5)
    assert label == "Risk-on" and score >= 60
    assert score == round(sum(c["pts"] for c in comps))


def test_stress_reads_risk_off():
    s = stats(spy_last=80, sma50=100, sma200=95, vix=34, vix3m=30, tnx20=0.5, dxy20=4.0)
    score, label, comps = macro.risk_score(s, breadth20=20, credit20=-3.0)
    assert label == "Risk-off" and score <= -80
    assert next(c for c in comps if c["name"] == "VIX term structure")["pts"] == -10


def test_score_is_bounded_and_components_respect_their_caps():
    for s, b, c in ((stats(), 100, 50.0), (stats(vix=90, vix3m=40, spy_last=1), 0, -50.0)):
        score, _, comps = macro.risk_score(s, b, c)
        assert -100 <= score <= 100
        for comp in comps:
            assert abs(comp["pts"]) <= comp["max"]


def test_missing_inputs_score_zero_instead_of_crashing():
    score, label, comps = macro.risk_score({})
    assert (score, label, comps) == (0, "Mixed", [])


def test_sector_strength_folds_etfs_into_buckets():
    out = macro.sector_strength({"XLK": -14.0, "XLC": -4.0, "XLE": 2.0, "XLF": 5.0}, 2.0)
    assert out["Tech/Comms"]["rel20"] == pytest.approx(-11.0)     # mean(-14,-4) - 2
    assert out["Tech/Comms"]["tilt"] == -1.0                      # clamped
    assert out["Energy"] == {"rel20": 0.0, "tilt": 0.0}
    assert 0 < out["Financials"]["tilt"] < 1
    assert "Healthcare" not in out                                # no data -> absent


def test_snapshot_survives_failed_series():
    def fetch(sym):
        if sym in ("^VIX3M", "GC=F", "XLRE"):
            return None                       # simulated fetch failure
        base = {"^VIX": 18.0, "^TNX": 4.5, "^IRX": 4.0}.get(sym, 100.0)
        return {"rows": [{"c": c} for c in ramp(base, 0.01)]}

    snap = macro.snapshot(fetch, breadth20=55.0)
    assert set(snap["missing"]) == {"^VIX3M", "GC=F", "XLRE"}
    assert -100 <= snap["riskScore"] <= 100 and snap["riskLabel"] in ("Risk-on", "Mixed", "Risk-off")
    assert [t["sym"] for t in snap["tiles"]][:3] == ["SPY", "QQQ", "IWM"]
    assert all(t["sym"] != "GC=F" for t in snap["tiles"])
    assert snap["curve10y3m"] == pytest.approx(0.5)
    assert len(snap["sectors"]) == 7
