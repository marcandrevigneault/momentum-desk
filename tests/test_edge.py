"""Edge-screener machinery: statistics correctness + end-to-end on synthetic."""
from __future__ import annotations

from momentum_desk.backtest.data import MinuteBar
from momentum_desk.backtest.providers import SyntheticHistory
from momentum_desk.edge.screen import (
    ScreenConfig,
    _forward_r,
    _spearman,
    run_screen,
)


def test_spearman_monotonic():
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert _spearman(x, [10, 20, 30, 40, 50]) > 0.99
    assert _spearman(x, [50, 40, 30, 20, 10]) < -0.99


def test_spearman_handles_ties():
    # binary feature (lots of ties) must not blow up and stays in [-1, 1]
    x = [0.0, 0.0, 1.0, 1.0, 0.0, 1.0]
    y = [1.0, -1.0, 2.0, 0.5, -0.5, 1.5]
    ic = _spearman(x, y)
    assert -1.0 <= ic <= 1.0


def _bar(t, o, h, l, c, tod):
    return MinuteBar(t=t, o=o, h=h, l=l, c=c, v=1000, cum_volume=1000 * (t + 1), vwap=c, tod=tod)


def test_forward_r_target_and_stop():
    entry, stop = 10.0, 9.0  # risk = 1.0, 2R target = 12.0
    winner = [_bar(1, 10, 12.5, 10, 12.4, 571)]   # tags the target
    r, mfe, _ = _forward_r(entry, stop, winner, target_r=2.0)
    assert r == 2.0 and mfe >= 2.0

    loser = [_bar(1, 10, 10.1, 8.5, 8.6, 571)]    # hits the stop
    r, _, mae = _forward_r(entry, stop, loser, target_r=2.0)
    assert r == -1.0 and mae <= -1.0


def test_run_screen_end_to_end():
    provider = SyntheticHistory(days=40, session="premarket")
    res = run_screen(provider, ScreenConfig(session="premarket"))
    assert res.n_events > 0
    assert res.features, "expected at least one screened feature"
    for f in res.features:
        assert -1.0 <= f.ic <= 1.0
        assert f.n >= 10
