"""Insider event enrichment (market cap, sector, news) and conditioning filters."""
from __future__ import annotations

import urllib.error

from momentum_desk.backtest.client import CachedClient
from momentum_desk.insider.enrich import enrich_events, filter_events
from momentum_desk.insider.models import InsiderConfig, InsiderEvent


def mk_event(sym="ACME", trigger_day="2025-03-17", market_cap=None,
             sector=None, has_recent_news=False, news_headline="") -> InsiderEvent:
    return InsiderEvent(
        symbol=sym, trigger_day=trigger_day, total_value=100_000.0, n_insiders=1,
        top_role="ceo", conviction=0.5, market_cap=market_cap, sector=sector,
        has_recent_news=has_recent_news, news_headline=news_headline,
    )


def _client(fake, tmp_path):
    return CachedClient("https://api.polygon.io", "KEY", cache_dir=str(tmp_path),
                         max_per_min=0, fetch=fake)


# --- enrich_events: profile fill ---------------------------------------

def test_profile_fills_market_cap_and_sector(tmp_path):
    def fake(url):
        if "/v3/reference/tickers/" in url:
            return {"results": {"market_cap": 5_000_000_000, "sic_description": "Software"}}
        return {"results": []}

    client = _client(fake, tmp_path)
    cfg = InsiderConfig()
    events = enrich_events([mk_event()], client, cfg)
    assert events[0].market_cap == 5_000_000_000
    assert events[0].sector == "Software"


# --- enrich_events: news window ------------------------------------------

def test_news_inside_window_sets_flag(tmp_path):
    def fake(url):
        if "/v3/reference/tickers/" in url:
            return {"results": {}}
        return {"results": [{"title": "ACME wins big contract", "published_utc": "2025-03-15T13:00:00Z"}]}

    client = _client(fake, tmp_path)
    cfg = InsiderConfig(news_lookback_days=5)
    events = enrich_events([mk_event(trigger_day="2025-03-17")], client, cfg)
    assert events[0].has_recent_news is True
    assert events[0].news_headline == "ACME wins big contract"


def test_news_on_trigger_day_excluded_lookahead_guard(tmp_path):
    def fake(url):
        if "/v3/reference/tickers/" in url:
            return {"results": {}}
        return {"results": [{"title": "same-day news", "published_utc": "2025-03-17T09:00:00Z"}]}

    client = _client(fake, tmp_path)
    cfg = InsiderConfig(news_lookback_days=5)
    events = enrich_events([mk_event(trigger_day="2025-03-17")], client, cfg)
    assert events[0].has_recent_news is False
    assert events[0].news_headline == ""


def test_news_before_lookback_window_excluded(tmp_path):
    def fake(url):
        if "/v3/reference/tickers/" in url:
            return {"results": {}}
        return {"results": [{"title": "too old", "published_utc": "2025-03-01T09:00:00Z"}]}

    client = _client(fake, tmp_path)
    cfg = InsiderConfig(news_lookback_days=5)
    events = enrich_events([mk_event(trigger_day="2025-03-17")], client, cfg)
    assert events[0].has_recent_news is False


# --- enrich_events: error handling ---------------------------------------

def test_http_error_falls_back_to_defaults(tmp_path):
    def fake(url):
        raise urllib.error.HTTPError(url, 500, "Server Error", {}, None)  # type: ignore[arg-type]

    client = _client(fake, tmp_path)
    cfg = InsiderConfig()
    events = enrich_events([mk_event()], client, cfg)
    assert events[0].market_cap is None
    assert events[0].sector is None
    assert events[0].has_recent_news is False
    assert events[0].news_headline == ""


def test_client_none_is_no_op_copy():
    original = mk_event()
    events = enrich_events([original], None, InsiderConfig())
    assert events[0] == original
    assert events[0] is not original


# --- filter_events: market cap matrix -------------------------------------

def test_filter_small_cap_passes():
    events = [mk_event(market_cap=200_000_000)]
    cfg = InsiderConfig(max_market_cap=300_000_000)
    assert filter_events(events, cfg) == events


def test_filter_large_cap_fails():
    events = [mk_event(market_cap=5_000_000_000)]
    cfg = InsiderConfig(max_market_cap=300_000_000)
    assert filter_events(events, cfg) == []


def test_filter_no_cap_configured_passes_everything():
    events = [mk_event(market_cap=5_000_000_000)]
    cfg = InsiderConfig(max_market_cap=None)
    assert filter_events(events, cfg) == events


def test_filter_unknown_cap_passes_when_included():
    events = [mk_event(market_cap=None)]
    cfg = InsiderConfig(max_market_cap=300_000_000, include_unknown_cap=True)
    assert filter_events(events, cfg) == events


def test_filter_unknown_cap_excluded_when_not_included():
    events = [mk_event(market_cap=None)]
    cfg = InsiderConfig(max_market_cap=300_000_000, include_unknown_cap=False)
    assert filter_events(events, cfg) == []


# --- filter_events: news filter -------------------------------------------

def test_filter_news_any_passes_everything():
    events = [mk_event(has_recent_news=True), mk_event(has_recent_news=False)]
    cfg = InsiderConfig(news_filter="any")
    assert filter_events(events, cfg) == events


def test_filter_news_quiet_keeps_only_no_news():
    quiet = mk_event(has_recent_news=False)
    loud = mk_event(has_recent_news=True)
    cfg = InsiderConfig(news_filter="quiet")
    assert filter_events([quiet, loud], cfg) == [quiet]


def test_filter_news_with_news_keeps_only_news():
    quiet = mk_event(has_recent_news=False)
    loud = mk_event(has_recent_news=True)
    cfg = InsiderConfig(news_filter="with_news")
    assert filter_events([quiet, loud], cfg) == [loud]
