"""polygon.io market-data adapter.

Strategy that keeps API usage sane: one full-market snapshot call per poll
gives last/open/vwap/volume/prev-close for *every* US stock at once. We
pre-filter that to the price band and a minimum move, then enrich only the
survivors with data that changes slowly and is therefore cached:

  * shares outstanding (≈ float)         — daily cache, /v3/reference/tickers
  * 20-day average volume (for RVOL)     — daily cache, /v2/aggs daily bars
  * latest news headline                 — short cache, /v2/reference/news

Uses urllib only (no SDK dependency). Needs a polygon.io API key.

NOTE on "float": polygon exposes *shares outstanding*, not true public float
(which excludes locked-up insider/restricted shares). We use it as an
approximation; treat the low-float filter as "small share count," not exact.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from ..models import Snapshot
from ..scanner import ScanConfig

_BASE = "https://api.polygon.io"


@dataclass
class _Cache:
    shares_outstanding: dict[str, float]
    avg_vol_20d: dict[str, float]
    news: dict[str, tuple[float, str]]  # symbol -> (fetched_ts, headline)
    day_stamp: str = ""


class PolygonAdapter:
    name = "polygon"

    def __init__(self, api_key: str, scan_cfg: ScanConfig | None = None, timeout: float = 8.0,
                 max_enrich: int = 40) -> None:
        self._key = api_key
        self._cfg = scan_cfg or ScanConfig()
        self._timeout = timeout
        self._max_enrich = max_enrich   # cap survivors enriched/poll (bounds first-poll cost)
        self._cache = _Cache({}, {}, {})
        self._universe: list[str] = []

    # ---- HTTP helper ----
    def _get(self, path: str, params: dict | None = None) -> dict:
        params = dict(params or {})
        params["apiKey"] = self._key
        url = f"{_BASE}{path}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=self._timeout) as resp:
            return json.loads(resp.read().decode())

    def universe(self) -> list[str]:
        return self._universe

    def poll(self):
        """One full-market snapshot, pre-filter, then enrich survivors."""
        self._roll_day_cache()
        try:
            data = self._get("/v2/snapshot/locale/us/markets/stocks/tickers")
        except Exception as e:  # noqa: BLE001 — a feed hiccup shouldn't kill the loop
            print(f"[polygon] snapshot failed: {e}")
            return []

        now = time.time()
        c = self._cfg
        out: list[Snapshot] = []
        survivors: list[dict] = []

        for t in data.get("tickers", []):
            day = t.get("day") or {}
            prev = t.get("prevDay") or {}
            last = (t.get("lastTrade") or {}).get("p") or day.get("c") or 0.0
            prev_close = prev.get("c") or 0.0
            if last <= 0 or prev_close <= 0:
                continue
            # cheap pre-filter so we only enrich real candidates
            if not (c.min_price <= last <= c.max_price):
                continue
            gap = 100.0 * (last - prev_close) / prev_close
            if gap < c.min_gap_pct:
                continue
            survivors.append((gap, t))

        # enrich only the strongest gappers, to bound API calls per poll
        survivors.sort(key=lambda x: x[0], reverse=True)
        survivors = [t for _g, t in survivors[: self._max_enrich]]
        self._universe = [t["ticker"] for t in survivors]
        for t in survivors:
            sym = t["ticker"]
            day = t.get("day") or {}
            prev = t.get("prevDay") or {}
            last = (t.get("lastTrade") or {}).get("p") or day.get("c") or 0.0
            news_ts, headline = self._news(sym, now)
            out.append(
                Snapshot(
                    symbol=sym,
                    last=last,
                    prev_close=prev.get("c") or 0.0,
                    day_open=day.get("o") or last,
                    vwap=day.get("vw") or last,
                    cum_volume=int(day.get("v") or 0),
                    avg_volume_20d=self._avg_vol(sym),
                    float_shares=self._shares_outstanding(sym),
                    halted=False,  # LULD status needs a separate feed; wire later
                    has_news=bool(headline),
                    news_headline=headline,
                    ts=now,
                )
            )
        return out

    # ---- slow-changing enrichment, cached ----
    def _roll_day_cache(self) -> None:
        stamp = time.strftime("%Y-%m-%d")
        if stamp != self._cache.day_stamp:
            self._cache = _Cache({}, {}, {}, day_stamp=stamp)

    def _shares_outstanding(self, sym: str) -> float | None:
        if sym in self._cache.shares_outstanding:
            return self._cache.shares_outstanding[sym] or None
        val = 0.0
        try:
            r = self._get(f"/v3/reference/tickers/{sym}").get("results") or {}
            val = float(r.get("share_class_shares_outstanding")
                        or r.get("weighted_shares_outstanding") or 0.0)
        except Exception:  # noqa: BLE001
            val = 0.0
        self._cache.shares_outstanding[sym] = val
        return val or None

    def _avg_vol(self, sym: str) -> float:
        if sym in self._cache.avg_vol_20d:
            return self._cache.avg_vol_20d[sym]
        avg = 0.0
        try:
            end = time.strftime("%Y-%m-%d")
            start = time.strftime("%Y-%m-%d", time.localtime(time.time() - 40 * 86400))
            r = self._get(f"/v2/aggs/ticker/{sym}/range/1/day/{start}/{end}",
                          {"adjusted": "true", "sort": "desc", "limit": 20})
            vols = [bar.get("v", 0) for bar in (r.get("results") or [])]
            if vols:
                avg = sum(vols) / len(vols)
        except Exception:  # noqa: BLE001
            avg = 0.0
        self._cache.avg_vol_20d[sym] = avg
        return avg

    def _news(self, sym: str, now: float, ttl: float = 120.0) -> tuple[float, str]:
        cached = self._cache.news.get(sym)
        if cached and now - cached[0] < ttl:
            return cached
        headline = ""
        try:
            r = self._get("/v2/reference/news", {"ticker": sym, "limit": 1, "order": "desc"})
            results = r.get("results") or []
            if results:
                published = results[0].get("published_utc", "")
                # only count it as a live catalyst if it's from today
                if published[:10] == time.strftime("%Y-%m-%d"):
                    headline = results[0].get("title", "")
        except Exception:  # noqa: BLE001
            headline = ""
        self._cache.news[sym] = (now, headline)
        return now, headline
