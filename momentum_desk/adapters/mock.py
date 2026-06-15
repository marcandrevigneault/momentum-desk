"""Synthetic low-float gapper feed, so the whole pipeline runs with no market
data subscription and outside market hours. The numbers are fabricated but
shaped like the real thing: a few names gap up on news, run, get extended, and
occasionally halt. Useful for development, demos, and backtester sanity checks.

NOT a data source for real trading — it invents prices.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field

from ..models import Snapshot


@dataclass
class _Stock:
    symbol: str
    prev_close: float
    day_open: float
    float_shares: float
    avg_volume_20d: float
    has_news: bool
    news_headline: str
    start_volume: float = 0.0   # shares already traded by the time we tune in
    last: float = 0.0
    vwap: float = 0.0
    cum_volume: int = 0
    halted: bool = False
    _t0: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.last = self.day_open
        self.vwap = self.day_open
        self.cum_volume = int(self.start_volume)


class MockReplayAdapter:
    """Drives a handful of fabricated tickers through a plausible morning."""

    name = "mock"

    def __init__(self, seed: int | None = 7) -> None:
        self._rng = random.Random(seed)
        self._stocks = [
            _Stock("GNSX", prev_close=2.10, day_open=2.45, float_shares=4.5e6,
                   avg_volume_20d=8.0e5, has_news=True, start_volume=5.0e6,
                   news_headline="GNS Bio announces positive Phase 2 readout"),
            _Stock("BHAT", prev_close=1.05, day_open=1.18, float_shares=12.0e6,
                   avg_volume_20d=1.2e6, has_news=True, start_volume=8.0e6,
                   news_headline="Blue Hat signs $40M distribution deal"),
            _Stock("VRPX", prev_close=6.40, day_open=6.55, float_shares=22.0e6,
                   avg_volume_20d=2.1e6, has_news=False, start_volume=3.0e6,
                   news_headline=""),
            _Stock("ATNF", prev_close=3.30, day_open=4.90, float_shares=2.8e6,
                   avg_volume_20d=6.0e5, has_news=True, start_volume=4.0e6,
                   news_headline="180 Life subsidiary granted FDA fast track"),
            _Stock("CEAD", prev_close=4.75, day_open=4.80, float_shares=9.5e6,
                   avg_volume_20d=4.0e5, has_news=False, start_volume=5.0e5,
                   news_headline=""),
        ]

    def universe(self) -> list[str]:
        return [s.symbol for s in self._stocks]

    def poll(self):
        now = time.time()
        out = []
        for s in self._stocks:
            self._step(s, now)
            out.append(
                Snapshot(
                    symbol=s.symbol, last=round(s.last, 2), prev_close=s.prev_close,
                    day_open=s.day_open, vwap=round(s.vwap, 3), cum_volume=s.cum_volume,
                    avg_volume_20d=s.avg_volume_20d, float_shares=s.float_shares,
                    halted=s.halted, has_news=s.has_news, news_headline=s.news_headline,
                    ts=now,
                )
            )
        return out

    def _step(self, s: _Stock, now: float) -> None:
        """One random-walk tick. News + low-float names get a stronger drift and
        fatter volume so they surface as runners; the rest mostly chop."""
        elapsed = now - s._t0
        drift = (0.012 if s.has_news else 0.0) + (0.006 if s.float_shares < 10e6 else 0.0)
        # momentum that fades after the first ~few minutes, like a real morning spike
        decay = math.exp(-elapsed / 180.0)
        shock = self._rng.gauss(drift * decay, 0.015)
        s.last = max(0.05, s.last * (1.0 + shock))

        vol_burst = self._rng.randint(2000, 9000)
        if s.has_news or s.float_shares < 10e6:
            vol_burst = int(vol_burst * self._rng.uniform(2.0, 5.0))
        s.cum_volume += vol_burst
        # incremental VWAP update
        s.vwap = (s.vwap * (s.cum_volume - vol_burst) + s.last * vol_burst) / max(s.cum_volume, 1)

        # rare LULD-style halt on a violent move
        s.halted = abs(shock) > 0.05 and self._rng.random() < 0.15
