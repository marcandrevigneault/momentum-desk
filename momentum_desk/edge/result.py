"""Shared result shape for account-level strategy runs.

Both the single-strategy account simulator (edge/portfolio.py) and the multi-leg
combo (edge/combo.py) produce a run over a shared-capital book — same capital
figures, capacity counts, equity curve and rollups. ``AccountRun`` is that common
shape; ``SimResult`` and ``ComboResult`` extend it with their specifics (the exit
policy / the per-leg attribution). This is the seed of the single
``StrategyResult`` the Strategy Lab will consume.

All fields carry defaults so subclasses can add their own required-in-practice
fields without tripping dataclass field-ordering; every construction site passes
them explicitly by keyword.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AccountRun:
    days: int = 0
    starting_equity: float = 0.0
    final_equity: float = 0.0
    n_signals: int = 0           # entries that triggered
    n_taken: int = 0             # entries we had capacity/capital to take
    n_skipped_capacity: int = 0
    metrics: dict = field(default_factory=dict)
    equity_curve: list[float] = field(default_factory=list)
    daily_equity: list[dict] = field(default_factory=list)
    monthly: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
