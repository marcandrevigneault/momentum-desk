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


def test_combo_concurrency_shared():
    # one shared book: a tight cap limits combined trades across both legs
    legs = [
        ComboLeg(name="a", provider=SyntheticHistory(days=40, session="intraday"), session="intraday"),
        ComboLeg(name="b", provider=SyntheticHistory(days=40, session="premarket"), session="premarket"),
    ]
    tight = run_combo(legs, ComboConfig(max_concurrent=1), RiskConfig())
    loose = run_combo(legs, ComboConfig(max_concurrent=10), RiskConfig())
    assert tight.n_taken <= loose.n_taken
