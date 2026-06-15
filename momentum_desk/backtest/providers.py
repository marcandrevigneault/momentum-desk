"""Two historical providers behind the same interface.

SyntheticHistory — fabricates gapper days and intraday paths so the backtester
runs with no API key. Randomness is neutral (not tuned to print profits); its
P&L is meaningless as evidence — it exists to exercise and verify the engine.

PolygonHistory — real point-in-time data from polygon.io: one grouped-daily
call per day reconstructs the gapper universe (open vs prior close, in band),
then minute aggregates drive the intraday simulation. Approximations are noted.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import urllib.parse
import urllib.request

from .data import DayCandidate, MinuteBar

_HEADLINES = [
    "announces positive trial data", "signs distribution agreement",
    "reports record quarterly revenue", "receives FDA clearance",
    "announces $25M registered direct offering", "unveils strategic partnership",
]


class SyntheticHistory:
    """Fabricated low-float gappers. Deterministic given the seed."""

    name = "synthetic"

    def __init__(self, days: int = 40, seed: int = 11) -> None:
        self._rng = random.Random(seed)
        self._days = self._make_days(days)
        self._pool = ["GNSX", "BHAT", "ATNF", "CEAD", "VRPX", "TOPS", "MULN", "NAOV", "GROM", "COSM"]
        self._cand_cache: dict[str, list[DayCandidate]] = {}
        self._min_cache: dict[tuple[str, str], list[MinuteBar]] = {}

    def _make_days(self, n: int) -> list[str]:
        out, d = [], dt.date(2025, 1, 6)  # a Monday
        while len(out) < n:
            if d.weekday() < 5:
                out.append(d.isoformat())
            d += dt.timedelta(days=1)
        return out

    def trading_days(self) -> list[str]:
        return list(self._days)

    def candidates(self, day: str) -> list[DayCandidate]:
        if day in self._cand_cache:
            return self._cand_cache[day]
        rng = random.Random(f"{day}")  # stable per day
        k = rng.randint(2, 4)
        picks = rng.sample(self._pool, k)
        cands = []
        for sym in picks:
            prev_close = round(rng.uniform(1.2, 12.0), 2)
            gap = rng.uniform(0.08, 0.9)
            day_open = round(prev_close * (1 + gap), 2)
            has_news = rng.random() < 0.8
            cands.append(DayCandidate(
                symbol=sym, day=day, prev_close=prev_close, day_open=day_open,
                avg_volume_20d=rng.uniform(3e5, 2.5e6),
                float_shares=rng.uniform(2e6, 25e6),
                has_news=has_news,
                news_headline=f"{sym} {rng.choice(_HEADLINES)}" if has_news else "",
            ))
        self._cand_cache[day] = cands
        return cands

    def minutes(self, symbol: str, day: str) -> list[MinuteBar]:
        key = (symbol, day)
        if key in self._min_cache:
            return self._min_cache[key]
        cand = next((c for c in self.candidates(day) if c.symbol == symbol), None)
        if cand is None:
            return []
        rng = random.Random(f"{day}:{symbol}")
        # day-type: some gappers follow through, most fade — neutral, not rigged
        roll = rng.random()
        drift = rng.uniform(0.0008, 0.004) if roll < 0.42 else -rng.uniform(0.0005, 0.0035)
        vol = rng.uniform(0.006, 0.018)

        price = cand.day_open
        # a real gapper has already traded multiples of its daily average by the
        # open (premarket), and keeps printing heavy volume — that's what makes
        # RVOL high. Seed and accrue accordingly.
        cum_v = int(cand.avg_volume_20d * rng.uniform(3.0, 9.0))
        pv = price * cum_v
        bars: list[MinuteBar] = []
        for t in range(90):  # ~first 90 minutes
            o = price
            ret = rng.gauss(drift, vol)
            c = max(0.05, o * (1 + ret))
            hi = max(o, c) * (1 + abs(rng.gauss(0, 0.004)))
            lo = min(o, c) * (1 - abs(rng.gauss(0, 0.004)))
            bv = int(max(2000, rng.gauss(cand.avg_volume_20d / 40, cand.avg_volume_20d / 80)))
            cum_v += bv
            pv += c * bv
            bars.append(MinuteBar(t=t, o=round(o, 4), h=round(hi, 4), l=round(lo, 4),
                                  c=round(c, 4), v=bv, cum_volume=cum_v, vwap=round(pv / cum_v, 4)))
            price = c
        self._min_cache[key] = bars
        return bars


class PolygonHistory:
    """Real point-in-time history from polygon.io (urllib, no SDK)."""

    name = "polygon-history"
    _BASE = "https://api.polygon.io"

    def __init__(self, api_key: str, days: int = 30, min_gap_pct: float = 10.0,
                 min_price: float = 1.0, max_price: float = 20.0, timeout: float = 15.0) -> None:
        self._key = api_key
        self._n = days
        self._min_gap = min_gap_pct
        self._min_price, self._max_price = min_price, max_price
        self._timeout = timeout
        self._grouped: dict[str, dict] = {}   # day -> {sym: bar}
        self._avg_cache: dict[str, float] = {}

    def _get(self, path: str, params: dict | None = None) -> dict:
        params = dict(params or {})
        params["apiKey"] = self._key
        url = f"{self._BASE}{path}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=self._timeout) as r:
            return json.loads(r.read().decode())

    def trading_days(self) -> list[str]:
        days, d = [], dt.date.today() - dt.timedelta(days=1)
        while len(days) < self._n:
            if d.weekday() < 5:
                days.append(d.isoformat())
            d -= dt.timedelta(days=1)
        return list(reversed(days))

    def _grouped_day(self, day: str) -> dict[str, dict]:
        if day not in self._grouped:
            try:
                r = self._get(f"/v2/aggs/grouped/locale/us/market/stocks/{day}", {"adjusted": "true"})
                self._grouped[day] = {b["T"]: b for b in (r.get("results") or [])}
            except Exception as e:  # noqa: BLE001
                print(f"[polygon-history] grouped {day} failed: {e}")
                self._grouped[day] = {}
        return self._grouped[day]

    def candidates(self, day: str) -> list[DayCandidate]:
        days = self.trading_days()
        if day not in days:
            return []
        prior = days[days.index(day) - 1] if days.index(day) > 0 else None
        if prior is None:
            return []
        today, yday = self._grouped_day(day), self._grouped_day(prior)
        out = []
        for sym, bar in today.items():
            prev = yday.get(sym)
            if not prev:
                continue
            prev_close, day_open = prev.get("c", 0), bar.get("o", 0)
            if prev_close <= 0 or not (self._min_price <= day_open <= self._max_price):
                continue
            gap = 100.0 * (day_open - prev_close) / prev_close
            if gap < self._min_gap:
                continue
            out.append(DayCandidate(
                symbol=sym, day=day, prev_close=prev_close, day_open=day_open,
                avg_volume_20d=prev.get("v", 0) or 1,  # prior-day volume proxy; refine w/ 20d aggs
                float_shares=None,   # shares-outstanding lookup omitted in batch backtest
                has_news=False,      # point-in-time news backfill is a separate pass
            ))
        return out

    def minutes(self, symbol: str, day: str) -> list[MinuteBar]:
        try:
            r = self._get(f"/v2/aggs/ticker/{symbol}/range/1/minute/{day}/{day}",
                          {"adjusted": "true", "sort": "asc", "limit": 50000})
        except Exception as e:  # noqa: BLE001
            print(f"[polygon-history] minutes {symbol} {day} failed: {e}")
            return []
        results = r.get("results") or []
        bars, cum_v, pv, t0 = [], 0, 0.0, None
        for i, b in enumerate(results):
            if t0 is None:
                t0 = b["t"]
            cum_v += int(b.get("v", 0))
            pv += b.get("vw", b.get("c", 0)) * b.get("v", 0)
            bars.append(MinuteBar(
                t=int((b["t"] - t0) / 60000), o=b["o"], h=b["h"], l=b["l"], c=b["c"],
                v=int(b.get("v", 0)), cum_volume=cum_v, vwap=round(pv / cum_v, 4) if cum_v else b["c"],
            ))
            if i > 120:  # first ~2h is plenty for an opening-range strategy
                break
        return bars
