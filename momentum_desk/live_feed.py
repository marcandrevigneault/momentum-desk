"""Turn the live feed's per-tick Snapshots into closed MinuteBars — the exact
shape the reconciled engine (``SymbolTracker.on_bar``) consumes.

The backtester feeds the engine one *completed* minute bar at a time, where
``cum_volume`` and ``vwap`` are running session totals and ``tod`` is the ET
minute-of-day. The live feed instead hands us point-in-time ``Snapshot``s every
couple of seconds. This aggregator buckets those snapshots by ET minute and,
when a minute rolls over, emits the just-closed bar — OHLC built from the
snapshots' ``last`` price, with ``cum_volume``/``vwap`` taken straight off the
last snapshot in the minute (they are already running session totals, exactly
what a real-time bar knows).

A bar is emitted only once its minute has closed (the first snapshot of the next
minute triggers it), mirroring the backtest which only ever sees closed bars.
``flush`` releases the final, still-open minute at end of session.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .backtest.data import MinuteBar
from .models import Snapshot

_ET = ZoneInfo("America/New_York")


def tod_of(ts: float) -> int:
    """ET minute-of-day for an epoch-seconds timestamp (570 = 09:30)."""
    dt = datetime.fromtimestamp(ts, tz=_ET)
    return dt.hour * 60 + dt.minute


@dataclass
class _Bucket:
    """The minute currently being accumulated for one symbol."""

    tod: int
    o: float
    h: float
    l: float
    c: float
    cum_volume: int
    vwap: float


class MinuteBarAggregator:
    """Snapshot stream → closed MinuteBars, per symbol. Stateful across ticks.

    ``ingest`` returns the bar that just *closed* (or None if the current minute
    is still building); ``flush``/``flush_all`` release the final open minute.
    """

    def __init__(self) -> None:
        self._bucket: dict[str, _Bucket] = {}
        self._first_tod: dict[str, int] = {}
        self._last_cum: dict[str, int] = {}

    def ingest(self, snap: Snapshot) -> MinuteBar | None:
        """Fold one snapshot in; return a just-closed MinuteBar if the minute
        rolled over, else None."""
        tod = tod_of(snap.ts)
        sym = snap.symbol
        cur = self._bucket.get(sym)

        if cur is None:
            self._bucket[sym] = _new_bucket(snap, tod)
            self._first_tod.setdefault(sym, tod)
            return None

        if tod == cur.tod:
            cur.h = max(cur.h, snap.last)
            cur.l = min(cur.l, snap.last)
            cur.c = snap.last
            cur.cum_volume = max(cur.cum_volume, snap.cum_volume)
            cur.vwap = snap.vwap
            return None

        if tod < cur.tod:
            # clock moved backwards (new session / out-of-order tick) — reset.
            self._bucket[sym] = _new_bucket(snap, tod)
            self._first_tod[sym] = tod
            self._last_cum.pop(sym, None)
            return None

        # minute advanced — close the prior bucket and start a fresh one.
        bar = self._emit(sym, cur)
        self._bucket[sym] = _new_bucket(snap, tod)
        return bar

    def flush(self, symbol: str) -> MinuteBar | None:
        """Emit the final, still-open minute for one symbol (end of session)."""
        cur = self._bucket.pop(symbol, None)
        if cur is None:
            return None
        return self._emit(symbol, cur)

    def flush_all(self) -> list[MinuteBar]:
        return [bar for sym in list(self._bucket) if (bar := self.flush(sym)) is not None]

    # ---- internals --------------------------------------------------------

    def _emit(self, symbol: str, b: _Bucket) -> MinuteBar:
        prev_cum = self._last_cum.get(symbol, 0)
        v = max(0, b.cum_volume - prev_cum)
        self._last_cum[symbol] = b.cum_volume
        return MinuteBar(
            t=b.tod - self._first_tod.get(symbol, b.tod),
            o=b.o, h=b.h, l=b.l, c=b.c, v=v,
            cum_volume=b.cum_volume, vwap=b.vwap, tod=b.tod,
        )


def _new_bucket(snap: Snapshot, tod: int) -> _Bucket:
    return _Bucket(tod=tod, o=snap.last, h=snap.last, l=snap.last, c=snap.last,
                   cum_volume=snap.cum_volume, vwap=snap.vwap)
