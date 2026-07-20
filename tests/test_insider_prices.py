"""Daily-bar providers: SyntheticDaily's determinism/OHLC/weekday guarantees
(a later CI smoke drives simulations off it, so these are load-bearing), and
PolygonDaily's parsing + error handling via an injected CachedClient."""
from __future__ import annotations

import http.client
import urllib.error

from momentum_desk.backtest.client import CachedClient
from momentum_desk.insider.prices import DailyBar, PolygonDaily, SyntheticDaily


def test_synthetic_daily_is_deterministic_across_instances():
    a = SyntheticDaily(days=60, seed=7).daily("ACME")
    b = SyntheticDaily(days=60, seed=7).daily("ACME")
    assert a == b


def test_synthetic_daily_differs_by_symbol_and_seed():
    base = SyntheticDaily(days=60, seed=7).daily("ACME")
    other_symbol = SyntheticDaily(days=60, seed=7).daily("OTHER")
    other_seed = SyntheticDaily(days=60, seed=8).daily("ACME")
    assert base != other_symbol
    assert base != other_seed


def test_synthetic_daily_ohlc_is_self_consistent():
    bars = SyntheticDaily(days=252, seed=7).daily("ACME")
    assert len(bars) == 252
    for bar in bars:
        assert isinstance(bar, DailyBar)
        assert bar.l <= min(bar.o, bar.c) <= max(bar.o, bar.c) <= bar.h
        assert bar.v > 0


def test_synthetic_daily_prices_in_expected_start_band():
    # first bar's open is the seeded starting price, ~$5-$80 per the brief
    bars = SyntheticDaily(days=5, seed=7).daily("ACME")
    assert 5.0 <= bars[0].o <= 80.0


def test_synthetic_daily_trading_days_are_weekdays_only_and_ascending():
    provider = SyntheticDaily(days=60, seed=7)
    days = provider.trading_days()
    assert len(days) == 60
    assert days == sorted(days)
    assert len(set(days)) == len(days)
    import datetime as dt
    for d in days:
        assert dt.date.fromisoformat(d).weekday() < 5
    assert days[-1] == "2026-07-17"


def test_synthetic_daily_bars_match_trading_days():
    provider = SyntheticDaily(days=10, seed=7)
    days = provider.trading_days()
    bars = provider.daily("ACME")
    assert [b.day for b in bars] == days


def test_synthetic_daily_name():
    assert SyntheticDaily().name == "synthetic"


def test_polygon_daily_parses_results(tmp_path):
    def fake(url):
        return {
            "results": [
                {"t": 1752624000000, "o": 10.0, "h": 11.0, "l": 9.5, "c": 10.5, "v": 123456},
                {"t": 1752710400000, "o": 10.5, "h": 12.0, "l": 10.0, "c": 11.5, "v": 654321},
            ]
        }
    client = CachedClient("https://api.polygon.io", "KEY", cache_dir=str(tmp_path),
                          max_per_min=0, fetch=fake)
    provider = PolygonDaily("KEY", days=10, client=client)
    bars = provider.daily("ACME")
    assert bars == [
        DailyBar(day="2025-07-16", o=10.0, h=11.0, l=9.5, c=10.5, v=123456),
        DailyBar(day="2025-07-17", o=10.5, h=12.0, l=10.0, c=11.5, v=654321),
    ]


def test_polygon_daily_name_and_cache_dir(tmp_path):
    provider = PolygonDaily("KEY", days=10, client=CachedClient(
        "https://api.polygon.io", "KEY", cache_dir=str(tmp_path), max_per_min=0, fetch=lambda url: {}))
    assert provider.name == "polygon"


def test_polygon_daily_returns_empty_on_404(tmp_path):
    def fake(url):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
    client = CachedClient("https://api.polygon.io", "KEY", cache_dir=str(tmp_path),
                          max_per_min=0, fetch=fake)
    provider = PolygonDaily("KEY", days=10, client=client)
    assert provider.daily("UNKNOWN") == []


def test_polygon_daily_returns_empty_on_other_http_error(tmp_path):
    def fake(url):
        raise urllib.error.HTTPError(url, 500, "Server Error", {}, None)  # type: ignore[arg-type]
    client = CachedClient("https://api.polygon.io", "KEY", cache_dir=str(tmp_path),
                          max_per_min=0, fetch=fake)
    provider = PolygonDaily("KEY", days=10, client=client)
    assert provider.daily("ACME") == []


def test_polygon_daily_trading_days_uses_reference_symbol(tmp_path):
    seen_paths = []
    def fake(url):
        seen_paths.append(url)
        return {"results": [{"t": 1752624000000, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]}
    client = CachedClient("https://api.polygon.io", "KEY", cache_dir=str(tmp_path),
                          max_per_min=0, fetch=fake)
    provider = PolygonDaily("KEY", days=10, client=client)
    days = provider.trading_days()
    assert days == ["2025-07-16"]
    assert any("/SPY/" in u for u in seen_paths)


def test_polygon_daily_client_defaults_when_none_given(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = PolygonDaily("KEY", days=10)
    assert isinstance(provider._client, CachedClient)
    assert str(provider._client._dir) == "data/cache/polygon"


# --- malformed EDGAR symbols must not crash a real run --------------------
#
# EDGAR's ISSUERTRADINGSYMBOL sometimes leaks dirty values like
# "HEI, HEI.A" (comma + space) all the way to the price provider. Before
# the fix, that space/comma landed unencoded in the Polygon URL path and
# urllib raised http.client.InvalidURL ("URL can't contain control
# characters ... found at least ' '"), which PolygonDaily.daily only
# caught for urllib.error.HTTPError — so it propagated and 500'd
# /api/lab/run. daily() must URL-quote the symbol and treat any fetch
# failure as "no data for this symbol" ([]), not a hard crash.


def test_polygon_daily_quotes_dirty_symbol_into_url_path(tmp_path):
    seen_urls = []

    def fake(url):
        seen_urls.append(url)
        return {"results": []}

    client = CachedClient("https://api.polygon.io", "KEY", cache_dir=str(tmp_path),
                          max_per_min=0, fetch=fake)
    provider = PolygonDaily("KEY", days=10, client=client)
    provider.daily("HEI, HEI.A")
    assert seen_urls, "expected the client to be called"
    assert " " not in seen_urls[0]
    assert "," not in seen_urls[0]


def test_polygon_daily_returns_empty_on_invalid_url(tmp_path):
    def fake(url):
        # Reproduces the exact crash seen in production: a raw space in an
        # unquoted URL trips http.client's control-character guard.
        raise http.client.InvalidURL(
            "URL can't contain control characters. "
            "'/v2/aggs/ticker/HEI, HEI.A/range/1/day' (found at least ' ')"
        )

    client = CachedClient("https://api.polygon.io", "KEY", cache_dir=str(tmp_path),
                          max_per_min=0, fetch=fake)
    provider = PolygonDaily("KEY", days=10, client=client)
    assert provider.daily("HEI, HEI.A") == []


def test_polygon_daily_returns_empty_on_url_error(tmp_path):
    def fake(url):
        raise urllib.error.URLError("boom")

    client = CachedClient("https://api.polygon.io", "KEY", cache_dir=str(tmp_path),
                          max_per_min=0, fetch=fake)
    provider = PolygonDaily("KEY", days=10, client=client)
    assert provider.daily("ACME") == []
