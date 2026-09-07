"""The delay-sensitivity maths, and the brief's staleness guard.

delay_test.py is what settled the "am I late to the game?" question, so its return
conventions need to mean exactly what the write-up says they mean."""

import pytest

import delay_test as dt
import morning_brief as mb


def aligned_one(closes, opens=None, sym="AAA"):
    opens = opens or list(closes)
    highs = [c + 1 for c in closes]
    vols = [1_000_000] * len(closes)
    return {sym: (closes, highs, vols, 0, opens)}


# ---- ret_delayed ---------------------------------------------------------------------

def test_delay_zero_is_open_t1_to_open_t1_plus_hold():
    closes = [100.0 + i for i in range(20)]
    opens = [c - 0.5 for c in closes]
    a = aligned_one(closes, opens)
    r = dt.ret_delayed(a, "AAA", t=5, hold=5, delay=0, fixed_exit=False)
    assert r == pytest.approx(opens[11] / opens[6] - 1)


def test_shifted_exit_keeps_the_holding_period_constant():
    closes = [100.0 + i for i in range(30)]
    opens = list(closes)
    a = aligned_one(closes, opens)
    for d in range(4):
        r = dt.ret_delayed(a, "AAA", t=5, hold=5, delay=d, fixed_exit=False)
        # entry open[6+d], exit open[11+d] -> always 5 bars of a +1/bar series
        assert r == pytest.approx(opens[11 + d] / opens[6 + d] - 1)


def test_fixed_exit_shortens_the_hold_as_the_delay_grows():
    closes = [100.0 + i for i in range(30)]
    opens = list(closes)
    a = aligned_one(closes, opens)
    rs = [dt.ret_delayed(a, "AAA", t=5, hold=5, delay=d, fixed_exit=True)
          for d in range(4)]
    # Same exit bar, later entry, rising series -> strictly less return each time.
    assert rs == sorted(rs, reverse=True)
    assert all(r is not None for r in rs)


def test_ret_delayed_returns_none_past_the_end():
    a = aligned_one([100.0] * 8)
    assert dt.ret_delayed(a, "AAA", t=5, hold=5, delay=3, fixed_exit=False) is None


# ---- ret_parts -----------------------------------------------------------------------

def test_parts_split_gap_from_the_rest():
    closes = [100.0] * 20
    opens = list(closes)
    opens[6] = 102.0                       # gapped up overnight before we could buy
    a = aligned_one(closes, opens)
    p = dt.ret_parts(a, "AAA", t=5, hold=5)
    assert p["gap"] == pytest.approx(102.0 / 100.0 - 1)
    assert p["rest"] == pytest.approx(100.0 / 102.0 - 1)
    # gap and rest compound to the full close-to-exit move
    assert (1 + p["gap"]) * (1 + p["rest"]) == pytest.approx(1 + p["full"])


def test_parts_none_when_the_window_runs_off_the_end():
    a = aligned_one([100.0] * 6)
    assert dt.ret_parts(a, "AAA", t=5, hold=5) is None


# ---- strategy definitions -------------------------------------------------------------

def test_build_strategies_covers_every_traded_card():
    s = dt.build_strategies({})
    for key in ("valueDD", "weeklyDip", "value200"):
        assert key in s, key
    assert s["mom14"][2] == dt.HOLD14        # 14-day family holds 10 sessions
    assert s["valueDD"][2] == dt.HOLD
    assert s["momTop10"][1] is True          # regime gated
    assert s["weeklyDip"][1] is False        # always in


# ---- morning brief staleness ----------------------------------------------------------

def test_age_hours_and_formatting():
    import datetime
    gen = (datetime.datetime.now() - datetime.timedelta(hours=3)).strftime("%Y-%m-%d %H:%M")
    assert mb.data_age_hours(gen) == pytest.approx(3.0, abs=0.05)
    assert mb.fmt_age(0.5) == "30 min old"
    assert mb.fmt_age(3.0) == "3.0 h old"
    assert mb.fmt_age(72.0) == "3 days old"
    assert mb.data_age_hours("not a date") is None
    assert mb.fmt_age(None) == "unknown age"


def _payload(generated, **kw):
    D = {"generated": generated, "regimeOn": True, "breadth20": 60,
         "spyPx": 770.0, "spySma50": 750.0,
         "rows": [{"sym": "AAA", "entry": 100.0, "atr": 2.0, "sector": "Tech/Comms",
                   "stop": 97.0, "target": 104.0}],
         "strategies": [{"key": "valueDD", "picks": ["AAA"], "stopMult": 1.5,
                         "targetMult": 2.0, "health": {"status": "ok"}}]}
    D.update(kw)
    return D


def test_brief_flags_stale_data_loudly():
    import push_orders as po
    cfg = dict(po.DEFAULTS, strategies=["valueDD"], account_size=100_000)
    text, _orders, _info, stale = mb.build_brief(_payload("2026-01-01 08:00"), cfg)
    assert stale is True
    assert "STALE DATA" in text
    assert "DO NOT TRADE" in text


def test_brief_is_quiet_when_data_is_fresh():
    import datetime
    import push_orders as po
    cfg = dict(po.DEFAULTS, strategies=["valueDD"], account_size=100_000)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    text, orders, _info, stale = mb.build_brief(_payload(now), cfg)
    assert stale is False
    assert "STALE" not in text
    assert [o["sym"] for o in orders] == ["AAA"]


def test_brief_and_push_orders_agree_on_every_order():
    """If these two ever disagree the brief is lying about what will be sent."""
    import datetime
    import push_orders as po
    cfg = dict(po.DEFAULTS, strategies=["valueDD"], account_size=100_000)
    D = _payload(datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    brief_orders = mb.build_brief(D, cfg)[1]
    push_orders_, _info = po.build_orders(D, cfg)
    assert brief_orders == push_orders_
