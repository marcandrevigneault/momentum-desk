"""Bundles: the one-stop object the Strategy Lab hands to the insider
strategy's engine — "give me events, give me a price feed" — so
``run_insider_strategy`` (and Strategy/Lab dispatch, Task 6's job) never has
to know whether it's talking to fabricated synthetic data or a real EDGAR +
polygon pipeline.

``SyntheticInsiderBundle`` fabricates deterministic filings against
SyntheticDaily's own price feed and runs them through the REAL
build_events/filter_events pipeline (no enrichment client, so market_cap/
news stay at their dataclass defaults — a Lab config that filters on those,
like the small-cap-cluster canonical variant, will legitimately see zero
synthetic events; that's a synthetic-data limitation, not a bug).
``RealInsiderBundle`` wires EdgarStore + PolygonDaily + enrich_events behind
the same interface for live/backtest use; its network paths are exercised
only against injected fakes here, real network runs are a follow-up.
"""
from __future__ import annotations

import hashlib
import random
from datetime import date
from typing import TYPE_CHECKING, Protocol

from ..backtest.client import CachedClient
from ..risk import RiskConfig
from .edgar import EdgarStore
from .enrich import enrich_events, filter_events
from .models import InsiderConfig, InsiderEvent, InsiderFiling
from .prices import DailyProvider, PolygonDaily, SyntheticDaily
from .signals import build_events
from .simulate import InsiderResult, run_insider

if TYPE_CHECKING:
    from ..edge.strategy import Strategy


class InsiderBundle(Protocol):
    name: str

    def events(self, cfg: InsiderConfig) -> list[InsiderEvent]: ...

    def provider(self) -> DailyProvider: ...


# A fixed universe of fake tickers for the synthetic feed — distinct from any
# real symbol so fabricated filings never get confused with live data.
_SYMBOLS = [
    "ACME", "ZETA", "NOVA", "ORCA", "FLUX", "IRON", "SAGE", "VOLT",
    "CORE", "PEAK", "RISE", "TIDE", "GLOW", "MINT", "REEF", "DASH",
    "COVE", "LOFT", "PIER", "WISP", "FERN", "KILN", "MESA", "OPAL",
]

# (title, is_ceo, is_cfo, is_officer) — rotated per fabricated filing.
_ROLES = [
    ("Chief Executive Officer", True, False, False),
    ("Chief Financial Officer", False, True, False),
    ("SVP Operations", False, False, True),
    ("VP Engineering", False, False, True),
    ("Director", False, False, False),
]

_STEP_DAYS = 12       # ~2.4 trading weeks between a symbol's filings
_CLUSTER_PROB = 0.35  # chance a second insider joins within the window


def _seed_for(key: str, seed: int) -> int:
    """Deterministic per-key seed via hashlib — NEVER builtin hash(), which is
    randomized per-process (see prices.py's identical rationale)."""
    digest = hashlib.sha256(f"{key}:{seed}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


def _fabricate_one(rng: random.Random, symbol: str, day: str, n: int) -> InsiderFiling:
    title, is_ceo, is_cfo, is_officer = rng.choice(_ROLES)
    price = round(rng.uniform(10.0, 150.0), 2)
    value = rng.uniform(30_000.0, 500_000.0)
    shares = round(value / price, 2)
    return InsiderFiling(
        accession=f"syn-{symbol}-{n}", symbol=symbol, filed=day, trans_date=day,
        code="P", shares=shares, price=price, owner_name=f"{symbol}-insider-{n}",
        is_ceo=is_ceo, is_cfo=is_cfo, is_officer=is_officer or is_ceo or is_cfo,
        officer_title=title,
    )


def _fabricate_filings(symbols: list[str], trading_days: list[str], seed: int) -> list[InsiderFiling]:
    """Deterministic officer/CEO P-buys, ~1 per symbol every 2-3 weeks, values
    $30k-$500k, some clustered with a second insider a few days later."""
    filings: list[InsiderFiling] = []
    if not trading_days:
        return filings
    for symbol in symbols:
        rng = random.Random(_seed_for(symbol, seed))
        idx = rng.randint(0, _STEP_DAYS - 1)
        n = 0
        while idx < len(trading_days):
            filings.append(_fabricate_one(rng, symbol, trading_days[idx], n))
            n += 1
            if rng.random() < _CLUSTER_PROB:
                cluster_idx = min(idx + rng.randint(1, 6), len(trading_days) - 1)
                filings.append(_fabricate_one(rng, symbol, trading_days[cluster_idx], n))
                n += 1
            idx += _STEP_DAYS + rng.randint(-3, 5)
    return filings


class SyntheticInsiderBundle:
    """SyntheticDaily prices + deterministically fabricated filings run
    through the real build_events/filter_events pipeline. name="synthetic"."""

    name = "synthetic"

    def __init__(self, days: int = 252, seed: int = 7) -> None:
        self._provider = SyntheticDaily(days=days, seed=seed)
        self._filings = _fabricate_filings(_SYMBOLS, self._provider.trading_days(), seed)

    def provider(self) -> DailyProvider:
        return self._provider

    def events(self, cfg: InsiderConfig) -> list[InsiderEvent]:
        raw = build_events(self._filings, cfg, self._provider.trading_days())
        enriched = enrich_events(raw, None, cfg)   # no client: fields stay default
        return filter_events(enriched, cfg)


def _quarters_covering(start: date, end: date) -> list[tuple[int, int]]:
    """Every (year, quarter) from `start`'s year Q1 through `end`'s quarter,
    inclusive — over-covers a little (whole years instead of exact quarter
    boundaries at the start) but load_quarter is idempotent, so that's cheap
    and safe rather than fiddly."""
    out: list[tuple[int, int]] = []
    for year in range(start.year, end.year + 1):
        last_q = 4 if year != end.year else (end.month - 1) // 3 + 1
        for q in range(1, last_q + 1):
            out.append((year, q))
    return out


class RealInsiderBundle:
    """EdgarStore filings (loading quarters on demand for the price window
    plus a 3-year routine-filter lookback) + PolygonDaily + enrich_events via
    a polygon CachedClient. name="polygon"."""

    name = "polygon"

    def __init__(self, api_key: str, days: int = 252, *, edgar_db_path: str = "data/insider.db",
                 lookback_years: int = 3, max_per_min: float = 5,
                 store: EdgarStore | None = None, provider: PolygonDaily | None = None,
                 client: CachedClient | None = None) -> None:
        self._lookback_years = lookback_years
        self._store = store if store is not None else EdgarStore(edgar_db_path)
        self._provider = provider if provider is not None else PolygonDaily(
            api_key, days=days, max_per_min=max_per_min,
        )
        self._client = client if client is not None else CachedClient(
            "https://api.polygon.io", api_key, cache_dir="data/cache/polygon",
            max_per_min=max_per_min,
        )

    def provider(self) -> DailyProvider:
        return self._provider

    def events(self, cfg: InsiderConfig) -> list[InsiderEvent]:
        trading_days = self._provider.trading_days()
        if not trading_days:
            return []
        lookback_start = self._lookback_start(trading_days[0])
        end = date.fromisoformat(trading_days[-1])
        for year, quarter in _quarters_covering(lookback_start, end):
            self._store.load_quarter(year, quarter)
        filings = self._store.filings(start=lookback_start.isoformat(), end=trading_days[-1])
        raw = build_events(filings, cfg, trading_days)
        enriched = enrich_events(raw, self._client, cfg)
        return filter_events(enriched, cfg)

    def _lookback_start(self, window_start_day: str) -> date:
        # Jan 1 of (window-start year - lookback_years) — avoids a Feb-29
        # ValueError from `.replace(year=...)` landing on a non-leap year,
        # and over-covers the routine-filter lookback a little, which is
        # cheap (load_quarter is idempotent) rather than fiddly.
        year = date.fromisoformat(window_start_day).year - self._lookback_years
        return date(year, 1, 1)


def run_insider_strategy(strategy: Strategy, bundle: InsiderBundle, risk_cfg: RiskConfig) -> InsiderResult:
    """Build InsiderConfig from the strategy's `insider` dict (unknown keys
    dropped), pull events + a price feed from the bundle (which applies
    enrich+filter internally), and run the daily-bar simulator."""
    cfg = InsiderConfig(**{
        k: v for k, v in strategy.insider.items() if k in InsiderConfig.__dataclass_fields__
    })
    events = bundle.events(cfg)
    return run_insider(
        events, bundle.provider(), cfg, risk_cfg,
        max_concurrent=strategy.max_concurrent,
        max_gross_pct=strategy.max_gross_pct,
        slippage_pct=strategy.slippage_pct,
    )
