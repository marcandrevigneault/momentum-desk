"""Core data model: market snapshots in, ranked signals out.

Everything downstream (scanner, risk, dashboard) speaks these dataclasses, so a
new data feed only needs to emit `Snapshot`s and a new broker only needs to
consume `Order`s. Pure stdlib — the core runs with no third-party deps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


@dataclass
class Snapshot:
    """A point-in-time view of one ticker, from whatever data feed is attached."""

    symbol: str
    last: float                 # last trade price
    prev_close: float           # prior session close (for gap %)
    day_open: float             # today's session open
    vwap: float                 # session volume-weighted average price
    cum_volume: int             # shares traded so far today
    avg_volume_20d: float       # 20-day average daily volume (relative-volume baseline)
    float_shares: float | None = None   # tradable float, shares (None = unknown)
    halted: bool = False
    has_news: bool = False
    news_headline: str = ""
    ts: float = 0.0             # epoch seconds of this snapshot

    # ---- derived metrics (cheap, computed on demand) ----
    @property
    def gap_pct(self) -> float:
        if self.prev_close <= 0:
            return 0.0
        return 100.0 * (self.last - self.prev_close) / self.prev_close

    @property
    def change_from_open_pct(self) -> float:
        if self.day_open <= 0:
            return 0.0
        return 100.0 * (self.last - self.day_open) / self.day_open

    @property
    def extension_above_vwap_pct(self) -> float:
        """How far price is stretched above VWAP — the anti-chase metric."""
        if self.vwap <= 0:
            return 0.0
        return 100.0 * (self.last - self.vwap) / self.vwap

    @property
    def relative_volume(self) -> float:
        """RVOL: today's volume vs the 20-day average. >1 means unusually active."""
        if self.avg_volume_20d <= 0:
            return 0.0
        return self.cum_volume / self.avg_volume_20d

    @property
    def float_millions(self) -> float | None:
        return None if self.float_shares is None else self.float_shares / 1e6


class Flag(StrEnum):
    """Why a candidate is risky or disqualified — surfaced to the trader."""

    EXTENDED = "extended_above_vwap"        # already ran; chasing = exit liquidity
    THIN_BOOK = "you_would_be_the_liquidity"  # your size is too big for the tape
    HALTED = "halted"
    NO_CATALYST = "no_news_catalyst"
    UNKNOWN_FLOAT = "unknown_float"


@dataclass
class Signal:
    """A scored, ranked scan result. `blocking_flags` being non-empty means
    the scanner found the setup but is telling you NOT to chase it."""

    symbol: str
    score: float
    last: float
    gap_pct: float
    relative_volume: float
    extension_above_vwap_pct: float
    float_millions: float | None
    has_news: bool
    news_headline: str
    flags: list[Flag] = field(default_factory=list)
    ts: float = 0.0

    @property
    def actionable(self) -> bool:
        """True only if nothing disqualifying is flagged."""
        blocking = {Flag.EXTENDED, Flag.THIN_BOOK, Flag.HALTED}
        return not any(f in blocking for f in self.flags)
