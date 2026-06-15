"""Historical-data contract for the backtester + the records it produces.

A provider must answer three things, all point-in-time (no peeking at the
future): which days exist, which symbols gapped into the band on a given day,
and the intraday minute path for one symbol on one day. The engine does the
rest. The synthetic and polygon providers in `providers.py` both implement it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class DayCandidate:
    """What the scanner would have seen pre-open: a gapper, with the slow-moving
    context (prior close, float, 20d volume) known before the bell."""

    symbol: str
    day: str                 # YYYY-MM-DD
    prev_close: float
    day_open: float
    avg_volume_20d: float
    float_shares: float | None = None
    has_news: bool = False
    news_headline: str = ""

    @property
    def gap_pct(self) -> float:
        if self.prev_close <= 0:
            return 0.0
        return 100.0 * (self.day_open - self.prev_close) / self.prev_close


@dataclass
class MinuteBar:
    """One intraday minute. `cum_volume` and `vwap` are running session totals
    up to and including this bar — i.e. exactly what's knowable in real time."""

    t: int                   # minutes since the session open (0 = first bar)
    o: float
    h: float
    l: float
    c: float
    v: int                   # this bar's volume
    cum_volume: int          # session volume through this bar
    vwap: float              # session VWAP through this bar


@dataclass
class Trade:
    symbol: str
    day: str
    entry_t: int
    entry: float
    stop: float
    target: float
    shares: int
    exit_t: int
    exit: float
    pnl: float               # net of commissions
    r_multiple: float        # pnl / intended risk dollars
    exit_reason: str         # "target" | "stop" | "time"


@dataclass
class Metrics:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0       # positive number
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    expectancy: float = 0.0       # $/trade
    expectancy_r: float = 0.0     # R/trade
    total_pnl: float = 0.0
    return_pct: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0


@dataclass
class BacktestResult:
    metrics: Metrics
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    starting_equity: float = 0.0
    days: int = 0
    skipped_no_entry: int = 0     # candidates that never triggered / were filtered


@runtime_checkable
class HistoricalProvider(Protocol):
    name: str

    def trading_days(self) -> list[str]: ...
    def candidates(self, day: str) -> list[DayCandidate]: ...
    def minutes(self, symbol: str, day: str) -> list[MinuteBar]: ...
