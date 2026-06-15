"""Cached, rate-limited HTTP for historical data.

The free Massive/polygon tier allows ~5 requests/minute; a single backtest makes
far more (one grouped call per day + a minute-bar call per candidate + news),
and a parameter sweep or walk-forward replays the *same* history dozens of
times. So this client:

  * **throttles** to a configurable rate with 429 exponential back-off, and
  * **caches every response to disk** keyed by request, so re-runs and sweeps
    replay from disk at zero API cost.

Caching is what makes optimization on the free tier feasible: pay the API cost
once, then iterate for free. The `fetch`/`sleep`/`clock` callables are injectable
so the logic is testable without the network.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path


class RateLimiter:
    """Blocks until at least `min_interval` has elapsed since the last call."""

    def __init__(self, max_per_min: float, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.min_interval = 60.0 / max_per_min if max_per_min and max_per_min > 0 else 0.0
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        if self._last is not None:
            delta = self._clock() - self._last
            if delta < self.min_interval:
                self._sleep(self.min_interval - delta)
        self._last = self._clock()


class CachedClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        cache_dir: str = "data/cache",
        max_per_min: float = 5,
        max_retries: int = 4,
        fetch: Callable[[str], dict] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = base_url
        self._key = api_key
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._limiter = RateLimiter(max_per_min, sleep, clock)
        self._max_retries = max(1, max_retries)
        self._sleep = sleep
        self._fetch = fetch or self._http_fetch
        self.calls = 0          # count of underlying network fetches (cache misses)
        self.cache_hits = 0

    def _cache_key(self, path: str, params: dict) -> str:
        # apiKey is excluded so the cache is portable and keys don't leak secrets
        raw = path + "?" + urllib.parse.urlencode(sorted(params.items()))
        return hashlib.sha1(raw.encode()).hexdigest()

    def get_json(self, path: str, params: dict | None = None) -> dict:
        params = dict(params or {})
        f = self._dir / (self._cache_key(path, params) + ".json")
        if f.exists():
            self.cache_hits += 1
            return json.loads(f.read_text())
        data = self._fetch_with_retry(path, params)
        f.write_text(json.dumps(data))
        return data

    def _fetch_with_retry(self, path: str, params: dict) -> dict:
        q = dict(params)
        q["apiKey"] = self._key
        url = f"{self._base}{path}?{urllib.parse.urlencode(q)}"
        for attempt in range(self._max_retries):
            self._limiter.wait()
            try:
                self.calls += 1
                return self._fetch(url)
            except urllib.error.HTTPError as e:
                # 429 = rate limited: back off and retry. Anything else, or out
                # of retries, propagates to the caller (which skips that item).
                if e.code == 429 and attempt < self._max_retries - 1:
                    self._sleep(2.0 ** attempt)
                    continue
                raise
        raise RuntimeError("unreachable")

    @staticmethod
    def _http_fetch(url: str) -> dict:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read().decode())
