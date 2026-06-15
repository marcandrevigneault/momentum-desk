"""Exit-policy lab: per-policy simulation correctness + end-to-end."""
from __future__ import annotations

from momentum_desk.backtest.data import MinuteBar
from momentum_desk.backtest.providers import SyntheticHistory
from momentum_desk.edge.exits import ExitPolicy, run_exit_lab, simulate_exit
from momentum_desk.edge.screen import ScreenConfig


def _bar(o, h, l, c, vwap=None):
    return MinuteBar(t=0, o=o, h=h, l=l, c=c, v=1000, cum_volume=1000,
                     vwap=vwap if vwap is not None else c, tod=571)


def test_fixed_target_and_stop():
    entry, stop = 10.0, 9.0  # risk = 1.0
    fwd = [_bar(10, 12.5, 10, 12.4)]            # tags +2R target (12.0)
    r, reason, held = simulate_exit(entry, stop, [], fwd, ExitPolicy("t", "", target_r=2.0), 0.0)
    assert reason == "target" and r == 2.0 and held == 1

    fwd = [_bar(10, 10.1, 8.5, 8.6)]            # hits hard stop
    r, reason, _ = simulate_exit(entry, stop, [], fwd, ExitPolicy("t", "", target_r=2.0), 0.0)
    assert reason == "stop" and r == -1.0


def test_pct_trail_locks_in_gain():
    entry, stop = 10.0, 9.0
    # bar 1 runs to 13 (high-water), bar 2 dips to 11.6 → 10% trail = 13*0.9 = 11.7 hit
    fwd = [_bar(10, 13.0, 10, 12.8), _bar(12.8, 12.9, 11.6, 11.7)]
    r, reason, _ = simulate_exit(entry, stop, [], fwd, ExitPolicy("t", "", trail_kind="pct", trail_param=10.0), 0.0)
    assert reason == "trail"
    assert 1.6 < r < 1.8   # ~ (11.7 - 10)/1.0


def test_time_exit_when_nothing_triggers():
    entry, stop = 10.0, 9.0
    fwd = [_bar(10, 10.2, 9.8, 10.1), _bar(10.1, 10.3, 9.9, 10.25)]
    r, reason, held = simulate_exit(entry, stop, [], fwd, ExitPolicy("t", "", target_r=5.0), 0.0)
    assert reason == "time" and held == 2 and abs(r - 0.25) < 1e-9


def test_void_when_no_risk():
    r, reason, held = simulate_exit(10.0, 10.0, [], [_bar(10, 11, 9, 10)], ExitPolicy("t", ""), 0.0)
    assert reason == "void" and r == 0.0 and held == 0


def test_run_exit_lab_end_to_end():
    provider = SyntheticHistory(days=40, session="premarket")
    res = run_exit_lab(provider, ScreenConfig(session="premarket"), slippage_pct=0.3)
    assert res.n_events > 0
    assert len(res.policies) >= 8
    # ranked by expectancy, descending
    exps = [p.expectancy_r for p in res.policies]
    assert exps == sorted(exps, reverse=True)
    for p in res.policies:
        assert p.n == res.n_events
        assert 0.0 <= p.win_rate <= 1.0
