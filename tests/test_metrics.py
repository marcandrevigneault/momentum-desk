"""Direct coverage for the shared metrics core — the single source of truth that
the Backtester, account simulator, and combo engine all compute stats through."""
from __future__ import annotations

from momentum_desk.backtest.data import Trade
from momentum_desk.backtest.metrics import compute_metrics


def _t(pnl: float, r: float) -> Trade:
    return Trade(symbol="X", day="2026-01-02", entry_t=0, entry=10.0, stop=9.5, target=11.0,
                 shares=100, exit_t=5, exit=10.0, pnl=pnl, r_multiple=r, exit_reason="time")


def test_empty_trades_is_zeroed():
    m = compute_metrics([], [25_000], 25_000)
    assert m.trades == 0 and m.profit_factor == 0.0 and m.total_pnl == 0.0


def test_known_trade_set():
    trades = [_t(100, 2), _t(-50, -1), _t(200, 4), _t(-50, -1)]
    curve = [25_000, 25_100, 25_050, 25_250, 25_200]
    m = compute_metrics(trades, curve, 25_000)
    assert m.trades == 4 and m.wins == 2 and m.losses == 2
    assert m.win_rate == 50.0
    assert m.gross_profit == 300.0 and m.gross_loss == 100.0
    assert m.profit_factor == 3.0
    assert m.avg_win == 150.0 and m.avg_loss == -50.0
    assert m.total_pnl == 200.0
    assert m.expectancy == 50.0
    assert m.expectancy_r == 1.0          # (2-1+4-1)/4
    assert m.return_pct == 0.8            # 100*200/25000
    assert m.max_drawdown == 50.0         # peak 25250 -> 25200
    assert m.max_drawdown_pct == 0.2


def test_all_winners_infinite_pf():
    m = compute_metrics([_t(10, 1), _t(20, 2)], [25_000, 25_010, 25_030], 25_000)
    assert m.gross_loss == 0.0 and m.profit_factor == float("inf")
