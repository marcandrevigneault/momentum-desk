"""Combo strategies: shared-capital multi-leg book + per-leg attribution."""
from __future__ import annotations

from momentum_desk.backtest.providers import SyntheticHistory
from momentum_desk.edge.combo import ComboConfig, ComboLeg, run_combo
from momentum_desk.risk import RiskConfig


def test_combo_runs_and_attributes():
    legs = [
        ComboLeg(name="premarket", provider=SyntheticHistory(days=60, session="premarket"), session="premarket"),
        ComboLeg(name="intraday", provider=SyntheticHistory(days=60, session="intraday"), session="intraday"),
    ]
    res = run_combo(legs, ComboConfig(), RiskConfig(account_equity=25_000))
    assert set(res.legs) == {"premarket", "intraday"}
    # per-leg attribution sums to the total realized P&L
    assert abs(sum(res.leg_pnl.values()) - res.metrics["total_pnl"]) < 1.0
    assert sum(res.leg_trades.values()) == res.metrics["trades"]
    assert res.metrics["trades"] == res.n_taken
    assert res.equity_curve[0] == 25_000


def test_fade_short_exit_pnl():
    # a short that reverts down should book a positive R; risk = stop - entry
    from momentum_desk.backtest.data import MinuteBar
    from momentum_desk.edge.exits import POLICIES, simulate_fade_detail
    prior = [MinuteBar(t=0, o=10, h=10, l=10, c=10, v=1, cum_volume=1, vwap=10, tod=575)]
    # short at 10, stop 11 (risk 1). price falls to 8 → +2R on a 2R target (target = 8)
    fwd = [MinuteBar(t=1, o=10, h=10.1, l=7.9, c=8.0, v=1, cum_volume=2, vwap=9, tod=576)]
    f = simulate_fade_detail(10.0, 11.0, prior, fwd, POLICIES[1], 0.0)  # fixed_2r
    assert f.reason == "target" and f.r == 2.0 and f.exit_price == 8.0
    # if it runs UP into the stop instead, it's a -1R loss
    fwd_up = [MinuteBar(t=1, o=10, h=11.2, l=10, c=11.1, v=1, cum_volume=2, vwap=10, tod=576)]
    fl = simulate_fade_detail(10.0, 11.0, prior, fwd_up, POLICIES[1], 0.0)
    assert fl.reason == "stop" and fl.r == -1.0


def test_combo_with_fade_leg_runs():
    legs = [
        ComboLeg(name="intraday", provider=SyntheticHistory(days=60, session="intraday"), session="intraday"),
        ComboLeg(name="fade", provider=SyntheticHistory(days=60, session="intraday"), session="intraday",
                 style="fade", fade_min_move_pct=8.0, fade_min_ext_pct=3.0),
    ]
    res = run_combo(legs, ComboConfig(), RiskConfig(account_equity=25_000))
    assert set(res.legs) == {"intraday", "fade"}
    assert abs(sum(res.leg_pnl.values()) - res.metrics["total_pnl"]) < 1.0


def test_combo_concurrency_shared():
    # one shared book: a tight cap limits combined trades across both legs
    legs = [
        ComboLeg(name="a", provider=SyntheticHistory(days=40, session="intraday"), session="intraday"),
        ComboLeg(name="b", provider=SyntheticHistory(days=40, session="premarket"), session="premarket"),
    ]
    tight = run_combo(legs, ComboConfig(max_concurrent=1), RiskConfig())
    loose = run_combo(legs, ComboConfig(max_concurrent=10), RiskConfig())
    assert tight.n_taken <= loose.n_taken
