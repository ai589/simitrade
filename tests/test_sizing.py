"""The sizing chain in push_orders.py - it decides how much real money goes on each name.

The dashboard, the morning brief and the order pusher all have to agree; these pin the
arithmetic so they cannot drift apart silently."""

import pytest

import push_orders as po


CFG = dict(po.DEFAULTS)
CFG.update(account_size=100_000, risk_pct=1.0, max_heat_pct=6.0, max_per_sector=2,
           max_pos_pct=20, regime_scale=True, budget=0, entry_limit_slip_pct=0.5,
           strategies=["valueDD"], skip_health=["demoted", "research"])


def row(sym, entry=100.0, atr=2.0, sector="Tech/Comms"):
    return {"sym": sym, "entry": entry, "atr": atr, "sector": sector,
            "stop": entry - 3, "target": entry + 4}


def strat(key="valueDD", picks=(), **kw):
    st = {"key": key, "picks": list(picks), "stopMult": 1.5, "targetMult": 2.0,
          "health": {"status": "ok"}}
    st.update(kw)
    return st


# ---- levels --------------------------------------------------------------------------

def test_levels_long_uses_atr_multiples():
    stop, target = po.levels(row("AAA", entry=100.0, atr=2.0), strat())
    assert stop == pytest.approx(97.0)      # 100 - 1.5 * 2
    assert target == pytest.approx(104.0)   # 100 + 2.0 * 2


def test_levels_short_inverts():
    stop, target = po.levels(row("AAA", entry=100.0, atr=2.0),
                             strat(side="short"))
    assert stop == pytest.approx(103.0)
    assert target == pytest.approx(96.0)


def test_levels_falls_back_to_row_levels_without_atr():
    r = row("AAA")
    r["atr"] = None
    assert po.levels(r, strat()) == (r["stop"], r["target"])


# ---- regime multiplier ----------------------------------------------------------------

@pytest.mark.parametrize("regime_on,breadth,expected", [
    (True, 60, 1.0),     # trend up and broad -> full size
    (True, 40, 0.5),     # one of the two
    (False, 60, 0.5),
    (False, 40, 0.25),   # neither -> quarter size
])
def test_regime_mult(regime_on, breadth, expected):
    D = {"regimeOn": regime_on, "breadth20": breadth}
    assert po.regime_mult(D, CFG) == expected


def test_regime_mult_can_be_switched_off():
    cfg = dict(CFG, regime_scale=False)
    assert po.regime_mult({"regimeOn": False, "breadth20": 0}, cfg) == 1.0


# ---- share count ----------------------------------------------------------------------

def test_shares_risks_one_percent_of_the_account():
    # entry 100, stop 90 -> $10 at risk per share; 1% of 100k = $1,000 -> 100 shares.
    # (Stop chosen wide enough that the 20% position cap does not bind.)
    assert po.shares(row("AAA"), 90.0, basket=5, cfg=CFG, rm=1.0) == 100


def test_shares_position_cap_binds_on_a_tight_stop():
    # entry 100, stop 97 -> the 1% risk rule alone wants 333 shares ($33k), but the
    # 20% position cap allows only 200.
    assert po.shares(row("AAA"), 97.0, basket=5, cfg=CFG, rm=1.0) == 200


def test_shares_scales_with_the_regime_multiplier():
    full = po.shares(row("AAA"), 90.0, 5, CFG, 1.0)
    half = po.shares(row("AAA"), 90.0, 5, CFG, 0.5)
    assert half == pytest.approx(full / 2, abs=1)


def test_regime_multiplier_still_bites_when_the_position_cap_binds():
    # Regression: rm used to be applied BEFORE the position cap, so the cap put the size
    # straight back up and a risk-off x0.5 basket still carried full size.
    full = po.shares(row("AAA"), 97.0, 5, CFG, 1.0)
    half = po.shares(row("AAA"), 97.0, 5, CFG, 0.5)
    assert half < full
    assert half == pytest.approx(full / 2, abs=1)


def test_shares_capped_by_max_position_pct():
    # A tight stop would otherwise buy far more than 20% of the account.
    n = po.shares(row("AAA", entry=100.0), 99.99, 5, CFG, 1.0)
    assert n * 100.0 <= CFG["account_size"] * CFG["max_pos_pct"] / 100


def test_shares_respects_a_trading_budget():
    cfg = dict(CFG, budget=10_000)
    n = po.shares(row("AAA", entry=100.0), 97.0, basket=5, cfg=cfg, rm=1.0)
    assert n <= 10_000 / 5 / 100.0


def test_shares_zero_when_stop_equals_entry():
    assert po.shares(row("AAA", entry=100.0), 100.0, 5, CFG, 1.0) == 0


# ---- build_orders ---------------------------------------------------------------------

def payload(**kw):
    D = {"generated": "2026-09-06 08:00", "regimeOn": True, "breadth20": 60,
         "rows": [row("AAA"), row("BBB"), row("CCC", sector="Health"),
                  row("DDD", sector="Energy")],
         "strategies": [strat(picks=["AAA", "BBB", "CCC"])]}
    D.update(kw)
    return D


def test_build_orders_produces_one_order_per_pick():
    orders, info = po.build_orders(payload(), CFG)
    assert sorted(o["sym"] for o in orders) == ["AAA", "BBB", "CCC"]
    assert info["regimeMult"] == 1.0


def test_build_orders_limit_is_marketable_at_the_open():
    # A resting BUY limit has to sit ABOVE the signal close or it never fills on a gap up.
    orders, _ = po.build_orders(payload(), CFG)
    o = next(o for o in orders if o["sym"] == "AAA")
    assert o["limit"] == pytest.approx(100.0 * 1.005)
    assert o["side"] == "BUY"


def test_build_orders_applies_the_sector_cap():
    # Four picks, three of them Tech/Comms, max_per_sector=2 -> one Tech name dropped.
    D = payload(rows=[row("AAA"), row("BBB"), row("CCC"), row("DDD", sector="Energy")],
                strategies=[strat(picks=["AAA", "BBB", "CCC", "DDD"])])
    orders, _ = po.build_orders(D, CFG)
    tech = [o for o in orders if o["sector"] == "Tech/Comms"]
    assert len(tech) == 2
    assert any(o["sector"] == "Energy" for o in orders)


def test_build_orders_skips_names_already_held():
    D = payload(strategies=[strat(picks=["AAA", "BBB", "CCC"],
                                  held={"AAA": "2026-09-01"})])
    orders, _ = po.build_orders(D, CFG)
    assert "AAA" not in [o["sym"] for o in orders]


def test_build_orders_honours_the_kill_switch():
    D = payload(strategies=[strat(picks=["AAA", "BBB"], health={"status": "demoted"})])
    orders, info = po.build_orders(D, CFG)
    assert orders == []
    assert info["skippedHealth"] == ["valueDD"]


def test_build_orders_caps_portfolio_heat():
    # Six names at 1% risk each = 6% before the cap; tighten the cap to 3% and every
    # size should be scaled, not just the last few.
    syms = ["S%d" % i for i in range(6)]
    D = payload(rows=[row(s, sector="S%d" % i) for i, s in enumerate(syms)],
                strategies=[strat(picks=syms)])
    cfg = dict(CFG, max_heat_pct=3.0)
    orders, info = po.build_orders(D, cfg)
    assert info["heatScale"] < 1
    # Regression: the scale used to be cancelled by the position cap, so six names at a
    # 3% cap still risked 3.6% of the account.
    assert sum(o["risk"] for o in orders) <= cfg["account_size"] * 3.0 / 100


def test_never_trade_blocks_the_short_baskets():
    cfg = dict(CFG, strategies=["valueDD", "shortSpike"])
    with pytest.raises(SystemExit) as e:
        po.build_orders(payload(), cfg)
    assert "shortSpike" in str(e.value)


def test_default_traded_set_is_the_mean_reversion_trio():
    # Guards the 2026-09 switch away from the demoted momentum baskets.
    assert po.DEFAULTS["strategies"] == ["valueDD", "weeklyDip", "value200"]
    for k in ("shortSpike", "shortBlowoff", "shortOverext"):
        assert k in po.DEFAULTS["never_trade"]
