"""Conditioning context for insider events: market cap, sector, and recent news.

Raw insider filings say nothing about *why* the trade might work — a CEO
buying $50k of a $200B mega-cap is a rounding error, while the same buy in a
$150M micro-cap is a strong signal, and a cluster of buys right after bad
news reads differently than one in a quiet name. This module fills that
context in from polygon.io (ticker profile + news) via the shared
`CachedClient` (one cached request per symbol/day, same as prices.py), then
lets `filter_events` condition the event list on it before the simulator
ever sees them. Enrichment never raises: a flaky API or an unknown symbol
should shrink the signal (leave fields at their None/False defaults), not
blow up a backtest or the live loop.
"""
from __future__ import annotations

import datetime as dt
import urllib.parse
from dataclasses import replace

from ..backtest.client import CachedClient
from .models import InsiderConfig, InsiderEvent

_NEWS_LIMIT = 10


def enrich_events(
    events: list[InsiderEvent], client: CachedClient | None, cfg: InsiderConfig,
) -> list[InsiderEvent]:
    """Fill market_cap/sector/has_recent_news/news_headline per event.

    `client=None` is a no-op (returns copies, fields untouched) so callers
    that don't have network access (tests, offline synthetic runs) can skip
    enrichment without special-casing it. Any per-symbol failure — HTTP
    error, malformed response, unknown ticker — leaves that event's fields
    at their dataclass defaults rather than propagating.
    """
    if client is None:
        return [replace(e) for e in events]
    return [_enrich_one(e, client, cfg) for e in events]


def _enrich_one(event: InsiderEvent, client: CachedClient, cfg: InsiderConfig) -> InsiderEvent:
    market_cap, sector = _profile(event.symbol, client)
    has_news, headline = _news(event.symbol, event.trigger_day, client, cfg.news_lookback_days)
    return replace(
        event, market_cap=market_cap, sector=sector,
        has_recent_news=has_news, news_headline=headline,
    )


def _profile(symbol: str, client: CachedClient) -> tuple[float | None, str | None]:
    try:
        r = client.get_json(f"/v3/reference/tickers/{urllib.parse.quote(symbol, safe='')}")
        results = r.get("results") or {}
        return results.get("market_cap"), results.get("sic_description")
    except Exception:
        # Covers network/HTTP errors as well as malformed 200 bodies (a
        # non-dict `r` or `results` of the wrong shape raising AttributeError
        # on `.get`) — any of it should shrink the signal, not blow up.
        return None, None


def _news(
    symbol: str, trigger_day: str, client: CachedClient, lookback_days: int,
) -> tuple[bool, str]:
    start = (dt.date.fromisoformat(trigger_day) - dt.timedelta(days=lookback_days)).isoformat()
    try:
        r = client.get_json(
            "/v2/reference/news",
            {
                "ticker": symbol,
                "published_utc.gte": start,
                "published_utc.lt": trigger_day,
                "limit": _NEWS_LIMIT,
            },
        )
        # Lookahead guard: re-check the window ourselves rather than trusting
        # the remote query params — news published *on* trigger_day must
        # never count, since the strategy only trades on trigger_day using
        # prior information.
        for item in r.get("results") or []:
            published_day = str(item.get("published_utc", ""))[:10]
            if start <= published_day < trigger_day:
                return True, item.get("title", "")
        return False, ""
    except Exception:
        # Covers network/HTTP errors as well as malformed 200 bodies (a
        # non-dict `r`, or a `results` whose shape breaks the loop above).
        return False, ""


def filter_events(events: list[InsiderEvent], cfg: InsiderConfig) -> list[InsiderEvent]:
    """Condition events on market cap and recent-news filters from `cfg`."""
    return [e for e in events if _passes_cap(e, cfg) and _passes_news(e, cfg)]


def _passes_cap(event: InsiderEvent, cfg: InsiderConfig) -> bool:
    if cfg.max_market_cap is None:
        return True
    if event.market_cap is None:
        return cfg.include_unknown_cap
    return event.market_cap <= cfg.max_market_cap


def _passes_news(event: InsiderEvent, cfg: InsiderConfig) -> bool:
    if cfg.news_filter == "quiet":
        return not event.has_recent_news
    if cfg.news_filter == "with_news":
        return event.has_recent_news
    return True
