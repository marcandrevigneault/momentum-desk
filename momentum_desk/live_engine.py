"""Reconciled live engine — makes the LIVE entry/exit identical to the backtest.

The Lab/backtest entry is the session breakout (screen._find_event) and the exit
is the strategy's exit policy (edge.exits.simulate_exit_detail), both evaluated
point-in-time on a sequence of MinuteBars. This module drives those SAME functions
incrementally, one bar at a time, so a live stream produces exactly the trades the
backtester would — the prerequisite for trusting live results against the Lab.

A ``SymbolTracker`` is a per-symbol state machine (watch → holding → done). Feed
it bars as they close; it emits an EntrySignal when the breakout triggers and an
ExitSignal when the exit policy fires (or the hold window ends). The portfolio
caps / sizing / order routing live above this, in the trader loop.

`tests/test_live_engine.py` proves the reconciliation: replaying a day through the
trackers reproduces run_simulation's trades bar-for-bar.
"""
from __future__ import annotations

from dataclasses import dataclass

from .backtest.data import MARKET_OPEN_TOD, DayCandidate, MinuteBar
from .edge.exits import ExitPolicy, simulate_exit_detail
from .edge.screen import ScreenConfig, _find_event


@dataclass
class EntrySignal:
    symbol: str
    entry: float
    stop: float
    entry_tod: int


@dataclass
class ExitSignal:
    symbol: str
    exit_price: float
    exit_tod: int
    reason: str
    r: float


def _deadline(cfg: ScreenConfig) -> int:
    """The ET minute-of-day after which no more forward bars count — must match
    the fwd window _find_event uses for the session."""
    if cfg.session == "premarket":
        return MARKET_OPEN_TOD + cfg.max_hold_minutes
    if cfg.session == "intraday":
        return cfg.intraday_entry_cutoff_tod + cfg.max_hold_minutes
    return 10_000  # regular uses a count window; handled by running out of bars


class SymbolTracker:
    """Per-symbol streaming entry/exit, reusing the backtest's exact functions."""

    def __init__(self, cand: DayCandidate, cfg: ScreenConfig, policy: ExitPolicy,
                 slippage_pct: float = 0.3) -> None:
        self.cand = cand
        self.cfg = cfg
        self.policy = policy
        self.slip = slippage_pct
        self.bars: list[MinuteBar] = []
        self.state = "watch"           # watch | holding | done
        self.entry_idx = 0
        self.entry = 0.0
        self.stop = 0.0
        self._deadline = _deadline(cfg)

    @property
    def symbol(self) -> str:
        return self.cand.symbol

    def on_bar(self, bar: MinuteBar) -> EntrySignal | ExitSignal | None:
        self.bars.append(bar)
        if self.state == "watch":
            return self._maybe_enter()
        if self.state == "holding":
            return self._maybe_exit(end_of_day=False)
        return None

    def end_of_day(self) -> ExitSignal | None:
        """Force-close a still-open position on time (mirrors the backtest closing
        everything at the end of the session)."""
        if self.state == "holding":
            return self._maybe_exit(end_of_day=True)
        return None

    # ---- internals --------------------------------------------------------

    def _maybe_enter(self) -> EntrySignal | None:
        ev = _find_event(self.bars, self.cfg)
        if ev is None:
            return None
        entry_idx, entry, stop, _fwd = ev
        if entry - stop <= 0:
            self.state = "done"        # void stop — never a trade (as in run_simulation)
            return None
        self.entry_idx, self.entry, self.stop = entry_idx, entry, stop
        self.state = "holding"
        return EntrySignal(self.symbol, entry, stop, self.bars[entry_idx].tod)

    def _maybe_exit(self, end_of_day: bool) -> ExitSignal | None:
        # Re-derive the entry + the identically-bounded forward window from the
        # SAME function the backtest used, so prior/fwd match exactly.
        ev = _find_event(self.bars, self.cfg)
        if ev is None:                 # shouldn't happen once holding
            return None
        entry_idx, entry, stop, fwd = ev
        if not fwd:
            return None                # no forward bars yet
        prior = self.bars[: entry_idx + 1]
        fill = simulate_exit_detail(entry, stop, prior, fwd, self.policy, self.slip)
        window_closed = end_of_day or self.bars[-1].tod > self._deadline
        if fill.reason != "time" or window_closed:
            self.state = "done"
            return ExitSignal(self.symbol, fill.exit_price, fill.exit_tod, fill.reason, fill.r)
        return None
