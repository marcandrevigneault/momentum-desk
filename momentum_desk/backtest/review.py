"""Aggregate a backtest's trades into period breakdowns for review —
month-by-month and year-by-year P&L, trade count, win rate, and a running
cumulative. Trade-by-trade detail is already in the trade list itself.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from .data import Trade


def _aggregate(trades: Sequence[Trade], key: Callable[[Trade], str]) -> list[dict]:
    groups: dict[str, dict] = {}
    for t in trades:
        g = groups.setdefault(key(t), {"period": key(t), "trades": 0, "wins": 0, "pnl": 0.0})
        g["trades"] += 1
        g["wins"] += 1 if t.pnl > 0 else 0
        g["pnl"] += t.pnl
    out, cum = [], 0.0
    for period in sorted(groups):
        g = groups[period]
        cum += g["pnl"]
        g["win_rate"] = round(100.0 * g["wins"] / g["trades"], 1) if g["trades"] else 0.0
        g["pnl"] = round(g["pnl"], 2)
        g["cum_pnl"] = round(cum, 2)
        out.append(g)
    return out


def breakdowns(trades: Sequence[Trade]) -> dict[str, list[dict]]:
    """{'monthly': [...], 'yearly': [...]} — each row sorted chronologically
    with trades / wins / win_rate / pnl / cum_pnl."""
    return {
        "monthly": _aggregate(trades, lambda t: t.day[:7]),   # YYYY-MM
        "yearly": _aggregate(trades, lambda t: t.day[:4]),    # YYYY
    }
