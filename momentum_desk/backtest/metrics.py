"""Trade-list → Metrics. The single source of truth for backtest statistics.

Every engine that produces a list of trades + an equity curve (the Backtester,
the account simulator in edge/portfolio.py, the multi-leg combo in edge/combo.py)
computes its headline stats here, so expectancy/PF/drawdown mean exactly the same
thing everywhere. Trades only need a ``.pnl`` and an ``.r_multiple``.
"""
from __future__ import annotations

from .data import Metrics, Trade


def compute_metrics(trades: list[Trade], curve: list[float], start: float) -> Metrics:
    m = Metrics()
    m.trades = len(trades)
    if not trades:
        return m
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl <= 0]
    m.wins, m.losses = len(wins), len(losses)
    m.win_rate = round(100.0 * m.wins / m.trades, 1)
    m.gross_profit = round(sum(wins), 2)
    m.gross_loss = round(-sum(losses), 2)
    m.profit_factor = round(m.gross_profit / m.gross_loss, 2) if m.gross_loss > 0 else float("inf")
    m.avg_win = round(m.gross_profit / m.wins, 2) if m.wins else 0.0
    m.avg_loss = round(-m.gross_loss / m.losses, 2) if m.losses else 0.0
    m.total_pnl = round(sum(t.pnl for t in trades), 2)
    m.expectancy = round(m.total_pnl / m.trades, 2)
    m.expectancy_r = round(sum(t.r_multiple for t in trades) / m.trades, 3)
    m.return_pct = round(100.0 * m.total_pnl / start, 2) if start > 0 else 0.0

    peak = curve[0]
    max_dd = 0.0
    for eq in curve:
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    m.max_drawdown = round(max_dd, 2)
    m.max_drawdown_pct = round(100.0 * max_dd / peak, 2) if peak > 0 else 0.0
    return m
