"""Tests for the cached/throttled HTTP client: cache avoids refetch, the rate
limiter spaces calls, and 429s are retried with back-off — all without network."""
from __future__ import annotations

import urllib.error

import pytest

from momentum_desk.backtest.http import CachedClient, RateLimiter


def test_cache_hit_avoids_second_fetch(tmp_path):
    calls = {"n": 0}
    def fake(url):
        calls["n"] += 1
        return {"url": url, "ok": True}
    c = CachedClient("https://x", "KEY", cache_dir=str(tmp_path), max_per_min=0, fetch=fake)
    a = c.get_json("/v2/thing", {"a": 1})
    b = c.get_json("/v2/thing", {"a": 1})     # identical → served from disk
    assert a == b
    assert calls["n"] == 1 and c.cache_hits == 1


def test_different_params_fetch_separately(tmp_path):
    calls = {"n": 0}
    def fake(url):
        calls["n"] += 1
        return {"n": calls["n"]}
    c = CachedClient("https://x", "KEY", cache_dir=str(tmp_path), max_per_min=0, fetch=fake)
    c.get_json("/p", {"a": 1})
    c.get_json("/p", {"a": 2})
    assert calls["n"] == 2


def test_api_key_excluded_from_cache_key(tmp_path):
    # same path/params but different keys must hit the same cache entry
    fake = lambda url: {"v": 1}  # noqa: E731
    c1 = CachedClient("https://x", "KEY-A", cache_dir=str(tmp_path), max_per_min=0, fetch=fake)
    c1.get_json("/p", {"a": 1})
    calls = {"n": 0}
    def fake2(url):
        calls["n"] += 1
        return {"v": 2}
    c2 = CachedClient("https://x", "KEY-B", cache_dir=str(tmp_path), max_per_min=0, fetch=fake2)
    assert c2.get_json("/p", {"a": 1}) == {"v": 1}   # served from c1's cache
    assert calls["n"] == 0


def test_rate_limiter_sleeps_to_space_calls():
    slept = []
    # clock frozen at 0 → every call appears to need the full interval
    rl = RateLimiter(max_per_min=60, sleep=slept.append, clock=lambda: 0.0)  # 1s interval
    rl.wait()                # first call: no sleep
    rl.wait()                # second: must wait ~1s
    assert slept and slept[0] == pytest.approx(1.0)


def test_429_is_retried_then_succeeds(tmp_path):
    attempts = {"n": 0}
    def flaky(url):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]
        return {"ok": True}
    c = CachedClient("https://x", "KEY", cache_dir=str(tmp_path), max_per_min=0,
                     fetch=flaky, sleep=lambda _s: None)
    assert c.get_json("/p")["ok"] is True
    assert attempts["n"] == 2     # failed once, retried once


def test_non_429_error_propagates(tmp_path):
    def boom(url):
        raise urllib.error.HTTPError(url, 500, "Server Error", {}, None)  # type: ignore[arg-type]
    c = CachedClient("https://x", "KEY", cache_dir=str(tmp_path), max_per_min=0,
                     fetch=boom, sleep=lambda _s: None)
    with pytest.raises(urllib.error.HTTPError):
        c.get_json("/p")
