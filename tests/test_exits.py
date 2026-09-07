"""Exit rules - the paper log's live simulation and the backtest's mirror of it.

The stop-before-target rule is the conservative choice: a bar that touches both levels is
booked as a loss. It is also the single assumption that decides whether a strategy reads
as "ok" or "demoted", so it is worth a test that fails loudly if anyone flips it."""

import json

import pytest

import screener
import stop_test


HOLD = 5


def bars(closes, highs=None, lows=None, start_day=20_000):
    """Daily bars in screener's shape: t is a unix timestamp, one calendar day apart."""
    n = len(closes)
    highs = highs or [c + 1 for c in closes]
    lows = lows or [c - 1 for c in closes]
    return [{"t": (start_day + i) * 86400, "o": closes[i], "h": highs[i],
             "l": lows[i], "c": closes[i], "v": 1_000_000} for i in range(n)]


def run_paper(tmp_path, monkeypatch, closes, highs=None, lows=None, entry=100.0):
    """Drive update_paper_log() against a throwaway log file and return the one entry."""
    monkeypatch.setattr(screener, "PAPER_LOG", str(tmp_path / "picks_log.json"))
    b = bars(closes, highs, lows)
    today = screener.datetime.datetime.utcfromtimestamp(b[0]["t"]).strftime("%Y-%m-%d")
    datamap = {"AAA": b}
    strategies = [{"key": "test", "picks": ["AAA"], "holdDays": HOLD,
                   "stopMult": 1.5, "targetMult": 2.0}]
    results = [{"sym": "AAA", "entry": entry, "atr": 2.0}]
    # First call books the entry; second call (bars now "matured") evaluates it.
    screener.update_paper_log(datamap, strategies, results, today)
    screener.update_paper_log(datamap, [], results, today)
    with open(str(tmp_path / "picks_log.json"), encoding="utf-8") as f:
        return json.load(f)["entries"][0]


# ---- live paper log ------------------------------------------------------------------

def test_paper_entry_levels_come_from_atr(tmp_path, monkeypatch):
    e = run_paper(tmp_path, monkeypatch, [100.0] * 8)
    assert e["stop"] == pytest.approx(97.0)     # 100 - 1.5 * 2
    assert e["target"] == pytest.approx(104.0)  # 100 + 2.0 * 2


def test_paper_stop_fills_at_the_stop_not_the_low(tmp_path, monkeypatch):
    # Day 1 craters well through the stop; the log must still book 97.0, not the low.
    e = run_paper(tmp_path, monkeypatch,
                  closes=[100.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0],
                  lows=[99.0, 80.0, 89.0, 89.0, 89.0, 89.0, 89.0])
    assert e["status"] == "stopped"
    assert e["exitPx"] == pytest.approx(97.0)
    assert e["days"] == 1
    assert e["retPct"] == pytest.approx(-3.0)


def test_paper_target_fills_at_the_target(tmp_path, monkeypatch):
    e = run_paper(tmp_path, monkeypatch,
                  closes=[100.0, 110.0, 110.0, 110.0, 110.0, 110.0, 110.0],
                  highs=[101.0, 120.0, 111.0, 111.0, 111.0, 111.0, 111.0],
                  lows=[99.0, 105.0, 109.0, 109.0, 109.0, 109.0, 109.0])
    assert e["status"] == "target"
    assert e["exitPx"] == pytest.approx(104.0)
    assert e["retPct"] == pytest.approx(4.0)


def test_paper_stop_wins_when_one_bar_touches_both(tmp_path, monkeypatch):
    # THE conservative rule. A bar spanning 96 to 105 hits stop (97) and target (104);
    # it must be booked as the loss.
    e = run_paper(tmp_path, monkeypatch,
                  closes=[100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
                  highs=[101.0, 105.0, 101.0, 101.0, 101.0, 101.0, 101.0],
                  lows=[99.0, 96.0, 99.0, 99.0, 99.0, 99.0, 99.0])
    assert e["status"] == "stopped"
    assert e["retPct"] < 0


def test_paper_time_exit_uses_the_close_of_the_hold_th_bar(tmp_path, monkeypatch):
    e = run_paper(tmp_path, monkeypatch,
                  closes=[100.0, 100.5, 101.0, 101.5, 102.0, 102.5, 103.0])
    assert e["status"] == "time-exit"
    assert e["days"] == HOLD
    assert e["exitPx"] == pytest.approx(102.5)   # bar index 5 = 5 sessions after entry


def test_paper_sell_by_skips_the_weekend(tmp_path, monkeypatch):
    e = run_paper(tmp_path, monkeypatch, [100.0] * 8)
    assert e["sellBy"] == screener.add_trading_days(e["date"], HOLD)


# ---- backtest mirror (stop_test.simulate) ---------------------------------------------

def series(closes, highs=None, lows=None):
    return (closes, highs or [c + 1 for c in closes], lows or [c - 1 for c in closes],
            list(closes))


def test_simulate_matches_the_paper_log_stop_fill():
    cl, hi, lo, op = series([100.0] * 8)
    lo[1] = 80.0
    r, kind = stop_test.simulate(cl, hi, lo, op, t=0, hold=5, a=2.0,
                                 entry_mode="close", use_stops=True)
    assert kind == "stopped"
    assert r == pytest.approx(-3.0 / 100)     # stop at 100 - 1.5*2 = 97


def test_simulate_stop_before_target_on_the_same_bar():
    cl, hi, lo, op = series([100.0] * 8)
    hi[1], lo[1] = 200.0, 80.0                # touches both
    _r, kind = stop_test.simulate(cl, hi, lo, op, t=0, hold=5, a=2.0,
                                  entry_mode="close", use_stops=True)
    assert kind == "stopped"


def test_simulate_without_stops_holds_to_the_time_exit():
    cl, hi, lo, op = series([100.0, 50.0, 50.0, 50.0, 50.0, 120.0, 120.0, 120.0])
    lo[1] = 10.0                              # would have stopped out with stops on
    r, kind = stop_test.simulate(cl, hi, lo, op, t=0, hold=5, a=2.0,
                                 entry_mode="close", use_stops=False)
    assert kind == "time-exit"
    assert r == pytest.approx(120.0 / 100.0 - 1)


def test_simulate_nextopen_entry_uses_the_following_open():
    cl, hi, lo, op = series([100.0] * 8)
    op[1] = 105.0                             # gapped up before we could buy
    r, kind = stop_test.simulate(cl, hi, lo, op, t=0, hold=5, a=2.0,
                                 entry_mode="nextopen", use_stops=False)
    assert kind == "time-exit"
    assert r == pytest.approx(op[6] / 105.0 - 1)


def test_simulate_returns_none_past_the_end_of_the_series():
    cl, hi, lo, op = series([100.0] * 4)
    assert stop_test.simulate(cl, hi, lo, op, t=0, hold=5, a=2.0,
                              entry_mode="close", use_stops=False) is None


def test_atr_at_matches_screener_atr():
    # Same bars through both implementations must agree - the backtest's stop distance
    # has to be the one the live system would actually have used.
    closes = [100.0 + (i % 3) for i in range(80)]
    highs = [c + 1.5 for c in closes]
    lows = [c - 1.5 for c in closes]
    mine = stop_test.atr_at(highs, lows, closes, t=79, window=70)
    # atr_at's window of 70 spans TRs for bars 10..79, each against the previous close,
    # so screener.atr needs bars 9..79 to build the same 70 true ranges.
    theirs = screener.atr([{"h": highs[i], "l": lows[i], "c": closes[i]}
                           for i in range(9, 80)], 14)
    assert mine == pytest.approx(theirs, rel=1e-9)
