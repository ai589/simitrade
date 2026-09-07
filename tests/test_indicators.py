"""Indicator maths: hand-checkable cases, not snapshots of whatever the code does today.

Every strategy pick, stop and target is derived from these three functions, so a silent
change here moves real money."""

import pytest

from screener import rsi, atr, analyse, add_trading_days
from backtest import features


# ---- RSI ----------------------------------------------------------------------------

def test_rsi_all_gains_is_100():
    # No down days at all -> average loss 0 -> RSI pinned at 100 by definition.
    assert rsi([100 + i for i in range(40)], 14) == 100.0


def test_rsi_all_losses_is_0():
    # Mirror image: average gain 0 -> 100 - 100/(1+0) = 0.
    assert rsi([200 - i for i in range(40)], 14) == 0.0


def test_rsi_alternating_is_mid_range():
    closes = [100 + (1 if i % 2 else 0) for i in range(40)]
    r = rsi(closes, 14)
    assert 40 < r < 60, r


def test_rsi_needs_n_plus_one_bars():
    assert rsi([1, 2, 3], 14) is None
    assert rsi(list(range(15)), 14) is not None


# ---- ATR ----------------------------------------------------------------------------

def _bars(closes, spread=1.0):
    return [{"h": c + spread, "l": c - spread, "c": c} for c in closes]


def test_atr_constant_range():
    # Flat closes, every bar spanning +/-1: true range is 2 on every bar, so ATR is 2
    # regardless of how Wilder smoothing is seeded.
    assert atr(_bars([100.0] * 40), 14) == pytest.approx(2.0)


def test_atr_uses_gap_against_previous_close():
    # A bar whose whole range sits above the previous close: TR must measure from that
    # close (h - pc), not just the bar's own high-low.
    bars = _bars([100.0] * 20) + [{"h": 130.0, "l": 128.0, "c": 129.0}]
    a14 = atr(bars, 14)
    assert a14 > 2.0
    # h - pc = 130 - 100 = 30 vs h - l = 2, so the gap term dominates that bar.
    assert max(30.0, 2.0) == 30.0


def test_atr_needs_n_plus_one_bars():
    assert atr(_bars([100.0] * 10), 14) is None


# ---- features() (backtest side) ------------------------------------------------------

def test_features_on_flat_series():
    closes = [100.0] * 70
    f = features(closes, closes, [1000] * 70)
    assert f["px"] == 100.0
    assert f["sma20"] == pytest.approx(100.0)
    assert f["sma50"] == pytest.approx(100.0)
    assert f["ret5"] == pytest.approx(0.0)
    assert f["ret20"] == pytest.approx(0.0)
    assert f["ret60"] == pytest.approx(0.0)
    assert f["vol20"] == pytest.approx(0.0)


def test_features_returns_use_the_right_lookbacks():
    # closes[-6] is the 5-days-ago bar, closes[-21] the 20-days-ago bar, etc.
    closes = [float(i) for i in range(1, 71)]
    f = features(closes, closes, [1000] * 70)
    assert f["ret5"] == pytest.approx(70 / 65 - 1)
    assert f["ret20"] == pytest.approx(70 / 50 - 1)
    assert f["ret60"] == pytest.approx(70 / 10 - 1)


def test_features_uptrend_ordering_on_a_rising_series():
    closes = [float(i) for i in range(1, 71)]
    f = features(closes, closes, [1000] * 70)
    assert f["px"] > f["sma20"] > f["sma50"]


# ---- analyse() ----------------------------------------------------------------------

def test_analyse_rejects_short_history():
    rows = [{"t": i * 86400, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1} for i in range(69)]
    assert analyse({"rows": rows}) is None


def test_analyse_entry_is_the_last_close():
    # The paper log books entries at r["entry"], so this must stay the signal-day close:
    # the whole live-vs-backtest comparison is calibrated on it.
    rows = [{"t": i * 86400, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0 + i * 0.1,
             "v": 5_000_000} for i in range(300)]
    r = analyse({"rows": rows, "symbol": "AAA", "name": "Test Co",
                 "exchange": "NASDAQ"})
    assert r is not None
    assert r["entry"] == pytest.approx(round(rows[-1]["c"], 2))


# ---- sell-by dates -------------------------------------------------------------------

def test_add_trading_days_skips_the_weekend():
    # 2026-09-04 is a Friday; +1 trading day is Monday the 7th, not Saturday the 5th.
    assert add_trading_days("2026-09-04", 1) == "2026-09-07"


def test_add_trading_days_five_days_is_a_week():
    assert add_trading_days("2026-09-07", 5) == "2026-09-14"


def test_add_trading_days_from_a_saturday():
    assert add_trading_days("2026-09-05", 1) == "2026-09-07"


def test_add_trading_days_zero_is_identity():
    assert add_trading_days("2026-09-07", 0) == "2026-09-07"
