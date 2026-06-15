"""The scanner: turn a stream of snapshots into ranked, flagged signals.

This encodes the *documented* Warrior-style setup (low float + high relative
volume + a news catalyst, in a tradable price band) and — just as important —
the anti-chase guards that keep you from buying the part of the move where you
become exit liquidity.

Filters decide what's a candidate. Flags decide whether it's safe to act.
Scoring decides the order. None of it is a profit guarantee; it's a disciplined,
inspectable rule set you can later validate in the backtester.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Flag, Signal, Snapshot


@dataclass
class ScanConfig:
    # --- candidate band (what Ross-style runners look like) ---
    # band widened to $30 after calibrating vs Ross's actual trades (he took a
    # $24.77 runner our old $20 cap missed)
    min_price: float = 1.0
    max_price: float = 30.0
    max_float_millions: float = 20.0      # "low float"
    min_relative_volume: float = 5.0      # unusual activity vs 20d avg
    min_gap_pct: float = 10.0             # gapping into the day
    require_news: bool = True             # catalyst-driven only

    # --- anti-exit-liquidity guards ---
    max_extension_above_vwap_pct: float = 8.0   # past this = already chased
    block_when_extended: bool = True
    block_when_halted: bool = True


class ScannerEngine:
    def __init__(self, config: ScanConfig | None = None) -> None:
        self.config = config or ScanConfig()

    def evaluate(self, snap: Snapshot) -> Signal | None:
        """Return a Signal if `snap` is in the candidate band, else None.
        A returned Signal may still carry blocking flags (found, but don't chase).
        """
        c = self.config

        # --- hard candidate gate: price band + activity profile ---
        if not (c.min_price <= snap.last <= c.max_price):
            return None
        if snap.relative_volume < c.min_relative_volume:
            return None
        if snap.gap_pct < c.min_gap_pct:
            return None
        if c.require_news and not snap.has_news:
            return None
        # unknown float can't be confirmed "low float" — let it through but flag it

        flags: list[Flag] = []
        if snap.float_millions is None:
            flags.append(Flag.UNKNOWN_FLOAT)
        elif snap.float_millions > c.max_float_millions:
            return None  # confirmed high float — not the setup

        if not snap.has_news:
            flags.append(Flag.NO_CATALYST)
        if snap.halted and c.block_when_halted:
            flags.append(Flag.HALTED)
        if snap.extension_above_vwap_pct > c.max_extension_above_vwap_pct and c.block_when_extended:
            flags.append(Flag.EXTENDED)

        return Signal(
            symbol=snap.symbol,
            score=self._score(snap),
            last=snap.last,
            gap_pct=round(snap.gap_pct, 1),
            relative_volume=round(snap.relative_volume, 1),
            extension_above_vwap_pct=round(snap.extension_above_vwap_pct, 1),
            float_millions=None if snap.float_millions is None else round(snap.float_millions, 1),
            has_news=snap.has_news,
            news_headline=snap.news_headline,
            flags=flags,
            ts=snap.ts,
        )

    def scan(self, snapshots) -> list[Signal]:
        """Evaluate a batch and return actionable-first, score-descending."""
        signals = [s for s in (self.evaluate(x) for x in snapshots) if s is not None]
        signals.sort(key=lambda s: (s.actionable, s.score), reverse=True)
        return signals

    def _score(self, snap: Snapshot) -> float:
        """Composite rank. Rewards relative volume, a catalyst, and a tight
        float; *penalises* extension so fresh setups outrank chased ones."""
        c = self.config
        rvol = min(snap.relative_volume / max(c.min_relative_volume, 1e-9), 5.0)  # 0..5
        gap = min(snap.gap_pct / max(c.min_gap_pct, 1e-9), 5.0)                    # 0..5
        catalyst = 1.0 if snap.has_news else 0.0
        if snap.float_millions is None:
            float_bonus = 0.5
        else:
            float_bonus = max(0.0, 1.0 - snap.float_millions / max(c.max_float_millions, 1e-9))
        extension_penalty = max(0.0, snap.extension_above_vwap_pct) / 10.0
        return round(2.0 * rvol + 1.5 * gap + 2.0 * catalyst + 2.0 * float_bonus - 1.5 * extension_penalty, 2)
