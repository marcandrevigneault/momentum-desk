"""Tests for the month/year breakdown aggregation."""
from __future__ import annotations

from momentum_desk.backtest.data import Trade
from momentum_desk.backtest.review import breakdowns


def _t(day, pnl):
    return Trade(symbol="X", day=day, entry_t=0, entry=1.0, stop=0.9, target=1.2, shares=10,
                 exit_t=5, exit=1.1, pnl=pnl, r_multiple=pnl / 100, exit_reason="target")


def test_monthly_and_yearly_grouping_and_cumulative():
    trades = [_t("2024-01-05", 100), _t("2024-01-20", -40), _t("2024-02-10", 60),
              _t("2025-03-03", 200)]
    bd = breakdowns(trades)

    months = bd["monthly"]
    assert [m["period"] for m in months] == ["2024-01", "2024-02", "2025-03"]   # sorted
    jan = months[0]
    assert jan["trades"] == 2 and jan["wins"] == 1 and jan["win_rate"] == 50.0
    assert jan["pnl"] == 60.0 and jan["cum_pnl"] == 60.0
    assert months[1]["cum_pnl"] == 120.0           # running total carries across months
    assert months[2]["cum_pnl"] == 320.0

    years = bd["yearly"]
    assert [y["period"] for y in years] == ["2024", "2025"]
    assert years[0]["trades"] == 3 and years[0]["pnl"] == 120.0
    assert years[1]["pnl"] == 200.0


def test_empty_trades():
    bd = breakdowns([])
    assert bd == {"monthly": [], "yearly": []}
