"""Tests for the pre-market session: synthetic data carries time-of-day and
spans the open; entries happen before 09:30 and positions are held into the
open; more pre-market slippage never helps."""
from __future__ import annotations

from momentum_desk.backtest import Backtester, SyntheticHistory
from momentum_desk.backtest.engine import BacktestConfig

OPEN_FROM_PM_START = 330   # minutes from 04:00 to 09:30 (570 - 240)


def test_synthetic_premarket_has_tod_and_spans_the_open():
    prov = SyntheticHistory(days=5, session="premarket")
    day = prov.trading_days()[0]
    cand = prov.candidates(day)[0]
    bars = prov.minutes(cand.symbol, day)
    assert bars[0].tod == 240                       # starts at 04:00 ET
    assert any(b.tod == 570 for b in bars)          # reaches the 09:30 open
    assert any(b.tod > 570 for b in bars)           # and continues into the session


def test_entries_are_premarket_and_held_into_the_open():
    res = Backtester(SyntheticHistory(days=40, session="premarket"),
                     bt=BacktestConfig(session="premarket")).run()
    assert res.metrics.trades > 0
    # every entry is before 09:30 (entry tod = 240 + entry_t < 570)
    assert all(t.entry_t < OPEN_FROM_PM_START for t in res.trades)
    # at least one position is carried into/after the open
    assert any(t.exit_t >= OPEN_FROM_PM_START for t in res.trades)


def test_more_premarket_slippage_never_helps():
    prov = SyntheticHistory(days=40, session="premarket")
    lo = Backtester(prov, bt=BacktestConfig(session="premarket", use_anti_chase=False,
                                            premarket_slippage_pct=0.2)).run()
    hi = Backtester(prov, bt=BacktestConfig(session="premarket", use_anti_chase=False,
                                            premarket_slippage_pct=1.0)).run()
    assert hi.metrics.total_pnl <= lo.metrics.total_pnl


def test_time_of_day_exit_caps_the_hold():
    # force flat at 10:00 ET (tod 600 → 360 min after the 04:00 start)
    res = Backtester(SyntheticHistory(days=40, session="premarket"),
                     bt=BacktestConfig(session="premarket", time_exit_tod=600)).run()
    assert res.metrics.trades > 0
    assert all(t.exit_t <= 360 for t in res.trades)          # nothing held past 10:00
    assert any(t.exit_reason == "time-cap" for t in res.trades)  # the cap actually fires


def test_intraday_session_enters_after_the_open_on_hod_break():
    res = Backtester(SyntheticHistory(days=40, session="intraday"),
                     bt=BacktestConfig(session="intraday", intraday_min_move_pct=2.0)).run()
    assert res.metrics.trades > 0
    # intraday entries are after the 09:30 open: bars start at tod 570, so
    # entry_t (minutes from the first bar) is >= the opening-range base
    assert all(t.entry_t >= 5 for t in res.trades)


def test_regular_session_unaffected_by_premarket_changes():
    # the default (regular) path still produces trades — no regression
    res = Backtester(SyntheticHistory(days=40)).run()
    assert res.metrics.trades > 0
