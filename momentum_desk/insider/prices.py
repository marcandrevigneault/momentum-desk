"""Daily OHLCV bars for the insider strategy's price feed.

Two providers behind one interface: SyntheticDaily fabricates deterministic
random-walk bars so signal/backtest code (and CI smoke tests) can run with no
network or API key; PolygonDaily reuses the existing CachedClient (see
backtest/client.py) to pull real daily aggregates from polygon.io, one cached
request per symbol, so repeat runs and parameter sweeps replay from disk.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import random
import urllib.parse
from dataclasses import dataclass
from typing import Protocol

from ..backtest.client import CachedClient

_END_DAY = dt.date(2026, 7, 17)   # SyntheticDaily's fixed "today" — see brief


@dataclass
class DailyBar:
    day: str    # YYYY-MM-DD
    o: float
    h: float
    l: float
    c: float
    v: int


class DailyProvider(Protocol):
    name: str

    def trading_days(self) -> list[str]: ...

    def daily(self, symbol: str) -> list[DailyBar]: ...   # ascending by day, [] if unknown symbol


def _weekdays_ending(end: dt.date, n: int) -> list[str]:
    """The `n` most recent weekdays (Mon-Fri) up to and including `end`,
    ascending. No holiday calendar — good enough for a synthetic feed."""
    out: list[str] = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return list(reversed(out))


def _seed_for(symbol: str, seed: int) -> int:
    """Deterministic per-symbol seed. Python's built-in hash() of a str is
    randomized per-process (PYTHONHASHSEED) unless explicitly disabled, so
    `hash((symbol, seed))` would give different bars on every run/CI worker.
    A fixed digest keeps `random.Random` output identical across runs."""
    digest = hashlib.sha256(f"{symbol}:{seed}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


class SyntheticDaily:
    """Deterministic (seeded per symbol) random-walk daily bars over N
    weekdays ending 2026-07-17. Same symbol+seed always yields identical
    bars — a later task's CI smoke drives simulations off this generator, so
    that determinism is load-bearing, not cosmetic."""

    name = "synthetic"

    def __init__(self, days: int = 252, seed: int = 7) -> None:
        self._trading_days = _weekdays_ending(_END_DAY, days)
        self._seed = seed

    def trading_days(self) -> list[str]:
        return list(self._trading_days)

    def daily(self, symbol: str) -> list[DailyBar]:
        rng = random.Random(_seed_for(symbol, self._seed))
        price = rng.uniform(5.0, 80.0)
        bars: list[DailyBar] = []
        for day in self._trading_days:
            o = price
            c = max(0.01, o * (1 + rng.uniform(-0.03, 0.03)))
            hi = max(o, c) * (1 + rng.uniform(0.0, 0.01))
            lo = min(o, c) * (1 - rng.uniform(0.0, 0.01))
            v = int(rng.uniform(1e5, 5e6))
            bars.append(DailyBar(day=day, o=round(o, 4), h=round(hi, 4),
                                  l=round(lo, 4), c=round(c, 4), v=v))
            price = c
        return bars


class PolygonDaily:
    """Daily aggregates via /v2/aggs/ticker/{sym}/range/1/day/{frm}/{to}, one
    cached call per symbol (via the shared CachedClient, so sweeps replay
    from disk instead of re-hitting the API). trading_days() derives its
    calendar from a reference symbol (SPY) since polygon has no bare
    "list of trading days" endpoint. Cache dir data/cache/polygon, same as
    PolygonHistory, so both providers share one on-disk cache."""

    name = "polygon"
    _BASE = "https://api.polygon.io"
    _REF_SYMBOL = "SPY"

    def __init__(self, api_key: str, days: int = 252, max_per_min: float = 5,
                 client: CachedClient | None = None) -> None:
        self._days = days
        self._client = client or CachedClient(
            self._BASE, api_key, cache_dir="data/cache/polygon", max_per_min=max_per_min,
        )

    def trading_days(self) -> list[str]:
        return [bar.day for bar in self.daily(self._REF_SYMBOL)]

    def daily(self, symbol: str) -> list[DailyBar]:
        start, end = self._range()
        quoted = urllib.parse.quote(symbol, safe="")
        try:
            r = self._client.get_json(
                f"/v2/aggs/ticker/{quoted}/range/1/day/{start}/{end}",
                {"adjusted": "true", "sort": "asc", "limit": 5000},
            )
        except Exception:
            # Malformed EDGAR-derived symbols (e.g. "HEI, HEI.A" — a comma
            # and space, which urllib.request rejects with InvalidURL
            # before this even becomes an HTTP request) and any other
            # fetch failure both mean "no data for this symbol" per the
            # DailyProvider contract, not a reason to sink the whole run.
            return []
        bars = [
            DailyBar(
                day=dt.datetime.fromtimestamp(row["t"] / 1000, tz=dt.UTC).date().isoformat(),
                o=row["o"], h=row["h"], l=row["l"], c=row["c"], v=int(row.get("v", 0)),
            )
            for row in (r.get("results") or [])
        ]
        return bars[-self._days:]

    def _range(self) -> tuple[str, str]:
        end = dt.date.today()
        # pad well past `days` calendar days to comfortably cover weekends
        # and holidays for `days` worth of *trading* days
        start = end - dt.timedelta(days=int(self._days * 1.6) + 10)
        return start.isoformat(), end.isoformat()
