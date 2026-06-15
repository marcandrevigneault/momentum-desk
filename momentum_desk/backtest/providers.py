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
import random
from zoneinfo import ZoneInfo

from .data import MARKET_OPEN_TOD, PREMARKET_OPEN_TOD, DayCandidate, MinuteBar
from .http import CachedClient

_ET = ZoneInfo("America/New_York")


def _et_minute(epoch_ms: int) -> int:
    """Bar timestamp (ms UTC) → ET minute-of-day, DST-aware (240 = 04:00)."""
    d = dt.datetime.fromtimestamp(epoch_ms / 1000, tz=dt.UTC).astimezone(_ET)
    return d.hour * 60 + d.minute

_HEADLINES = [
    "announces positive trial data", "signs distribution agreement",
    "reports record quarterly revenue", "receives FDA clearance",
    "announces $25M registered direct offering", "unveils strategic partnership",
]


class SyntheticHistory:
    """Fabricated low-float gappers. Deterministic given the seed."""

    name = "synthetic"

    def __init__(self, days: int = 40, seed: int = 11, session: str = "regular") -> None:
        self._rng = random.Random(seed)
        self._days = self._make_days(days)
        self._pool = ["GNSX", "BHAT", "ATNF", "CEAD", "VRPX", "TOPS", "MULN", "NAOV", "GROM", "COSM"]
        self._session = session   # "regular" (09:30 on) | "premarket" (04:00 → into the open)
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
        # day-type: some gappers follow through, most fade — neutral, not rigged.
        # intraday-session names need bigger post-open swings to trip a HOD break,
        # so winners drift harder there (still ~half losers).
        roll = rng.random()
        if self._session == "intraday":
            drift = rng.uniform(0.002, 0.006) if roll < 0.45 else -rng.uniform(0.001, 0.004)
        else:
            drift = rng.uniform(0.0008, 0.004) if roll < 0.42 else -rng.uniform(0.0005, 0.0035)
        vol = rng.uniform(0.006, 0.018)

        # premarket: 04:00→~10:30 (390 bars). intraday: 09:30→~12:40 (190 bars,
        # room for a post-open HOD break + hold). regular: 09:30 + 90 min.
        premarket = self._session == "premarket"
        if premarket:
            start_tod, n_bars = PREMARKET_OPEN_TOD, 390
        elif self._session == "intraday":
            start_tod, n_bars = MARKET_OPEN_TOD, 190
        else:
            start_tod, n_bars = MARKET_OPEN_TOD, 90
        # thinner volume + wider noise before the open, like a real pre-market book
        vol *= 1.6 if premarket else 1.0

        price = cand.day_open
        cum_v = int(cand.avg_volume_20d * rng.uniform(3.0, 9.0))
        pv = price * cum_v
        bars: list[MinuteBar] = []
        for t in range(n_bars):
            tod = start_tod + t
            o = price
            ret = rng.gauss(drift, vol)
            c = max(0.05, o * (1 + ret))
            hi = max(o, c) * (1 + abs(rng.gauss(0, 0.004)))
            lo = min(o, c) * (1 - abs(rng.gauss(0, 0.004)))
            per_bar = cand.avg_volume_20d / (120 if premarket and tod < MARKET_OPEN_TOD else 40)
            bv = int(max(1000, rng.gauss(per_bar, per_bar / 2)))
            cum_v += bv
            pv += c * bv
            bars.append(MinuteBar(t=t, o=round(o, 4), h=round(hi, 4), l=round(lo, 4),
                                  c=round(c, 4), v=bv, cum_volume=cum_v, vwap=round(pv / cum_v, 4), tod=tod))
            price = c
        self._min_cache[key] = bars
        return bars


class PolygonHistory:
    """Real point-in-time history from polygon.io (urllib, no SDK)."""

    name = "polygon-history"
    _BASE = "https://api.polygon.io"

    def __init__(self, api_key: str, days: int = 30, min_gap_pct: float = 10.0,
                 min_price: float = 1.0, max_price: float = 30.0,
                 cache_dir: str = "data/cache/polygon", max_per_min: float = 5,
                 fetch_news: bool = True, max_candidates_per_day: int = 20,
                 universe_mode: str = "gap", min_rvol_universe: float = 3.0) -> None:
        self._n = days
        self._min_gap = min_gap_pct
        self._min_price, self._max_price = min_price, max_price
        self._fetch_news = fetch_news
        self._max_candidates = max_candidates_per_day
        self._universe_mode = universe_mode   # "gap" (open gappers) | "active" (high RVOL, any open)
        self._min_rvol_universe = min_rvol_universe
        self._grouped: dict[str, dict] = {}   # day -> {sym: bar}
        self._avg_cache: dict[str, float] = {}
        self._avg_cache_by_sym: dict[str, list[tuple[str, float]]] = {}  # sym -> [(date, volume)]
        # cached + throttled HTTP so sweeps replay from disk and we never get
        # 429'd off the free tier (see backtest/http.py)
        self.client = CachedClient(self._BASE, api_key, cache_dir=cache_dir, max_per_min=max_per_min)

    def _get(self, path: str, params: dict | None = None) -> dict:
        return self.client.get_json(path, params)

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
        rows = []   # {sym, prev_close, day_open, prior_vol, today_vol, high, low}
        for sym, bar in today.items():
            prev = yday.get(sym)
            if not prev:
                continue
            prev_close, day_open = prev.get("c", 0), bar.get("o", 0)
            if prev_close <= 0 or not (self._min_price <= day_open <= self._max_price):
                continue
            rows.append({"sym": sym, "prev_close": prev_close, "day_open": day_open,
                         "prior_vol": prev.get("v", 0) or 1, "today_vol": bar.get("v", 0) or 1,
                         "high": bar.get("h", 0), "low": bar.get("l", 0) or day_open})

        if self._universe_mode == "active":
            chosen = self._active_universe(rows, day)
        else:
            gappers = [r for r in rows if 100.0 * (r["day_open"] - r["prev_close"]) / r["prev_close"] >= self._min_gap]
            gappers.sort(key=lambda r: (r["day_open"] - r["prev_close"]) / r["prev_close"], reverse=True)
            chosen = gappers[: self._max_candidates]

        out = []
        for r in chosen:
            has_news, headline = self._premarket_news(r["sym"], day) if self._fetch_news else (False, "")
            out.append(DayCandidate(
                symbol=r["sym"], day=day, prev_close=r["prev_close"], day_open=r["day_open"],
                avg_volume_20d=self._avg_vol_20d(r["sym"], day, r["prior_vol"]),   # true trailing-20d avg
                float_shares=None,    # shares-outstanding (≈float) lookup omitted in batch backtest
                has_news=has_news, news_headline=headline,
            ))
        return out

    # how many names to RVOL-check per day; wide net so high-RVOL low-floats
    # aren't crowded out by big-cap volume before RVOL is even computed
    _ACTIVE_SUPERSET = 150

    def _active_universe(self, rows: list, day: str) -> list:
        """High-RVOL names regardless of open gap (for intraday momentum). RVOL
        (today vol / trailing-20d avg) is the signal — a low-float runner can be
        100x+. We can't compute it for every stock, so take a wide superset by
        the cheap proxies (raw volume ∪ intraday range), compute true RVOL for
        those, and keep the top by RVOL. Universe selection uses end-of-day stats
        to bound what to simulate; the intraday entry itself stays point-in-time."""
        for r in rows:
            r["range_pct"] = 100.0 * (r["high"] - r["low"]) / r["low"] if r["low"] > 0 else 0.0
        by_vol = sorted(rows, key=lambda r: r["today_vol"], reverse=True)[: self._ACTIVE_SUPERSET]
        by_rng = sorted(rows, key=lambda r: r["range_pct"], reverse=True)[: self._ACTIVE_SUPERSET]
        superset = {r["sym"]: r for r in by_vol + by_rng}.values()   # union, de-duped
        scored = []
        for r in superset:
            avg = self._avg_vol_20d(r["sym"], day, r["prior_vol"])
            rvol = r["today_vol"] / avg if avg > 0 else 0.0
            if rvol >= self._min_rvol_universe:
                scored.append((rvol, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _rv, r in scored[: self._max_candidates]]

    def _avg_vol_20d(self, sym: str, day: str, fallback: float) -> float:
        """True trailing-20-session average volume — one cached daily-aggs call
        per symbol for the whole run, vs the noisy prior-day proxy."""
        if sym not in self._avg_cache_by_sym:
            days = self.trading_days()
            start = (dt.date.fromisoformat(days[0]) - dt.timedelta(days=45)).isoformat()
            series: list[tuple[str, float]] = []
            try:
                r = self._get(f"/v2/aggs/ticker/{sym}/range/1/day/{start}/{days[-1]}",
                              {"adjusted": "true", "sort": "asc", "limit": 5000})
                for b in r.get("results") or []:
                    bd = dt.datetime.fromtimestamp(b["t"] / 1000, tz=dt.UTC).astimezone(_ET).date().isoformat()
                    series.append((bd, b.get("v", 0)))
            except Exception:  # noqa: BLE001
                series = []
            self._avg_cache_by_sym[sym] = series
        prior = [v for (bd, v) in self._avg_cache_by_sym[sym] if bd < day]
        if len(prior) >= 5:
            window = prior[-20:]
            return sum(window) / len(window)
        return fallback

    def _premarket_news(self, sym: str, day: str) -> tuple[bool, str]:
        """A catalyst counts if published in the ~20 h before the 09:30 ET open
        — captures the classic prior-afternoon/evening press release plus
        overnight/pre-market news, but never anything after the open (no
        lookahead)."""
        y, m, d = (int(x) for x in day.split("-"))
        open_utc = dt.datetime(y, m, d, 9, 30, tzinfo=_ET).astimezone(dt.UTC)
        gte = open_utc - dt.timedelta(hours=20)
        try:
            r = self._get("/v2/reference/news", {
                "ticker": sym, "order": "desc", "limit": 1,
                "published_utc.lte": open_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "published_utc.gte": gte.strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            res = r.get("results") or []
            if res:
                return True, res[0].get("title", "")
        except Exception:  # noqa: BLE001
            pass
        return False, ""

    def minutes(self, symbol: str, day: str) -> list[MinuteBar]:
        try:
            r = self._get(f"/v2/aggs/ticker/{symbol}/range/1/minute/{day}/{day}",
                          {"adjusted": "true", "sort": "asc", "limit": 50000})
        except Exception as e:  # noqa: BLE001
            print(f"[polygon-history] minutes {symbol} {day} failed: {e}")
            return []
        bars, cum_v, pv, t0 = [], 0, 0.0, None
        for b in r.get("results") or []:
            tod = _et_minute(b["t"])
            if tod < PREMARKET_OPEN_TOD or tod > 660:   # keep 04:00 → 11:00 ET
                continue
            if t0 is None:
                t0 = b["t"]
            cum_v += int(b.get("v", 0))
            pv += b.get("vw", b.get("c", 0)) * b.get("v", 0)
            bars.append(MinuteBar(
                t=int((b["t"] - t0) / 60000), o=b["o"], h=b["h"], l=b["l"], c=b["c"],
                v=int(b.get("v", 0)), cum_volume=cum_v, vwap=round(pv / cum_v, 4) if cum_v else b["c"],
                tod=tod,
            ))
        return bars
