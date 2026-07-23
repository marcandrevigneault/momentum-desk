"""Bundles: the one-stop object the Strategy Lab hands to the congress
strategy's engine — "give me events, give me a price feed" — so
``run_congress_strategy`` (and Strategy/Lab dispatch) never has to know
whether it's talking to fabricated synthetic data or a real kadoa + polygon
pipeline. Mirrors ``momentum_desk/insider/bundles.py`` closely and reuses
its daily-bar simulator (``insider.simulate.run_insider``) unchanged, since
``congress.signals.build_events`` already emits the shared ``InsiderEvent``
shape.

``SyntheticCongressBundle`` fabricates deterministic congress trades against
SyntheticDaily's own price feed and runs them through the REAL build_events
pipeline (no enrichment client, so market_cap/news stay at their dataclass
defaults). ``RealCongressBundle`` wires CongressStore + PolygonDaily +
insider's enrich_events behind the same interface for live/backtest use; its
network paths are exercised only against injected fakes here, real network
runs are a follow-up.
"""
from __future__ import annotations

import hashlib
import logging
import random
from dataclasses import asdict
from datetime import date, timedelta
from typing import TYPE_CHECKING, Protocol

from ..backtest.client import CachedClient
from ..insider.enrich import enrich_events
from ..insider.models import InsiderConfig, InsiderEvent
from ..insider.prices import DailyProvider, PolygonDaily, SyntheticDaily
from ..insider.simulate import InsiderResult, run_insider
from ..risk import RiskConfig
from .loader import CongressStore, CongressTrade
from .power import load_power
from .signals import CongressConfig, build_events

if TYPE_CHECKING:
    from ..edge.strategy import Strategy

_log = logging.getLogger(__name__)

# The only CongressConfig.owners values the STOCK Act disclosure schema (and
# loader.py's owner normalization) can ever produce — checked eagerly here so
# an unrecognized value (e.g. a stray client typo) raises a clear, catchable
# ValueError instead of surfacing deep inside build_events on a worker thread.
_KNOWN_OWNERS = {"SELF", "SP", "JT", "DC"}


class CongressBundle(Protocol):
    name: str

    def events(self, cfg: CongressConfig) -> list[InsiderEvent]: ...

    def provider(self) -> DailyProvider: ...


# A fixed universe of fake tickers for the synthetic feed — distinct from any
# real symbol (and from insider.bundles' own fake universe) so fabricated
# trades never get confused with live data or the insider strategy's own
# synthetic filings.
_SYMBOLS = [
    "ABLE", "BOLT", "CLAY", "DRUM", "ECHO", "FERN", "GLEN", "HAZE",
    "IVEY", "JADE", "KIVA", "LUME", "MOSS", "NOOK", "OARS", "PLUM",
    "QUAY", "RUNE", "SILT", "TERN", "URGE", "VANE", "WICK", "YARN",
]

# A tiny built-in fake power set, distinct from the real power.json roster,
# so the synthetic bundle's power_only path has something to match without
# depending on real curated data.
_POWER_FILER_IDS = frozenset({"house_synthetic_power_1", "senate_synthetic_power_2"})

# STOCK Act-style disclosure amount brackets. The first is the real modal
# $1,001-$15,000 bracket — deliberately included as "noise" so the default
# CongressConfig's min_amount floor (15,001) has something to drop.
_AMOUNT_BRACKETS = [
    (1_001.0, 15_000.0),
    (15_001.0, 50_000.0),
    (50_001.0, 100_000.0),
    (100_001.0, 250_000.0),
    (250_001.0, 500_000.0),
]

_STEP_DAYS = 15       # ~3 trading weeks between a symbol's filings
_CLUSTER_PROB = 0.35  # chance a second filer joins within the window


def _seed_for(key: str, seed: int) -> int:
    """Deterministic per-key seed via hashlib — NEVER builtin hash(), which is
    randomized per-process (see insider/prices.py's identical rationale)."""
    digest = hashlib.sha256(f"{key}:{seed}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


def _fabricate_one(
    rng: random.Random, symbol: str, chamber: str, filing_day: str, days_to_file: int, n: int,
) -> CongressTrade:
    filer_id = f"{chamber}_{symbol.lower()}_filer_{n}"
    if rng.random() < 0.06:
        filer_id = rng.choice(sorted(_POWER_FILER_IDS))

    # A sprinkle of noise rows (sale / dependent-owner / modal-bracket-small)
    # so the default filters (Purchase-only, owner in SELF/SP/JT, min_amount
    # 15,001) have something to legitimately drop.
    noise = rng.random()
    if noise < 0.08:
        transaction_type, owner = "Sale", "SELF"
        low, high = rng.choice(_AMOUNT_BRACKETS[1:])
    elif noise < 0.16:
        transaction_type, owner = "Purchase", "DC"
        low, high = rng.choice(_AMOUNT_BRACKETS[1:])
    elif noise < 0.24:
        transaction_type, owner = "Purchase", "SELF"
        low, high = _AMOUNT_BRACKETS[0]
    else:
        transaction_type = "Purchase"
        owner = rng.choice(["SELF", "SP", "JT"])
        low, high = rng.choice(_AMOUNT_BRACKETS[1:])

    trans_date = (date.fromisoformat(filing_day) - timedelta(days=days_to_file)).isoformat()
    return CongressTrade(
        filer_id=filer_id, member_name=f"{symbol} Member {n}", chamber=chamber,
        ticker=symbol, transaction_date=trans_date, filing_date=filing_day,
        owner=owner, asset_type="ST", transaction_type=transaction_type,
        amount_low=low, amount_high=high, is_late=False, days_to_file=days_to_file,
    )


def _fabricate_trades(symbols: list[str], trading_days: list[str], seed: int) -> list[CongressTrade]:
    """Deterministic congress disclosures, ~1 filing per symbol every 2-3
    weeks, amounts 15k-500k (plus a modal $1k-15k noise bracket), some
    clustered with a second (distinct) filer a few days later, a sprinkle of
    fake power-set filers, and a sprinkle of Sale/dependent/small-amount
    noise rows for the default filters to drop."""
    trades: list[CongressTrade] = []
    if not trading_days:
        return trades
    for i, symbol in enumerate(symbols):
        rng = random.Random(_seed_for(symbol, seed))
        chamber = "house" if i % 2 == 0 else "senate"
        idx = rng.randint(0, _STEP_DAYS - 1)
        n = 0
        while idx < len(trading_days):
            trades.append(_fabricate_one(
                rng, symbol, chamber, trading_days[idx], rng.randint(3, 40), n,
            ))
            n += 1
            if rng.random() < _CLUSTER_PROB:
                cluster_idx = min(idx + rng.randint(1, 6), len(trading_days) - 1)
                trades.append(_fabricate_one(
                    rng, symbol, chamber, trading_days[cluster_idx], rng.randint(3, 40), n,
                ))
                n += 1
            idx += _STEP_DAYS + rng.randint(-3, 5)
    return trades


class SyntheticCongressBundle:
    """SyntheticDaily prices + deterministically fabricated congress trades
    run through the real build_events pipeline. name="synthetic"."""

    name = "synthetic"

    def __init__(self, days: int = 252, seed: int = 7) -> None:
        self._provider = SyntheticDaily(days=days, seed=seed)
        self._trades = _fabricate_trades(_SYMBOLS, self._provider.trading_days(), seed)
        self.power: set[str] = set(_POWER_FILER_IDS)

    def provider(self) -> DailyProvider:
        return self._provider

    def events(self, cfg: CongressConfig) -> list[InsiderEvent]:
        power = self.power if cfg.power_only else None
        raw = build_events(self._trades, cfg, self._provider.trading_days(), power=power)
        return enrich_events(raw, None, InsiderConfig())   # no client: fields stay default


class RealCongressBundle:
    """CongressStore.refresh() (network-wrapped: a failure logs and degrades
    to whatever the store already has, rather than crashing the run — unless
    the store has nothing usable either, in which case it's an honest
    ValueError instead of a silently empty run) + PolygonDaily.
    power=load_power(). name="polygon"."""

    name = "polygon"

    def __init__(self, api_key: str, days: int = 252, *, congress_db_path: str = "data/congress.db",
                 max_per_min: float = 5, store: CongressStore | None = None,
                 provider: PolygonDaily | None = None, client: CachedClient | None = None,
                 power: set[str] | None = None) -> None:
        self._store = store if store is not None else CongressStore(congress_db_path)
        self._provider = provider if provider is not None else PolygonDaily(
            api_key, days=days, max_per_min=max_per_min,
        )
        self._client = client if client is not None else CachedClient(
            "https://api.polygon.io", api_key, cache_dir="data/cache/polygon",
            max_per_min=max_per_min,
        )
        self.power: set[str] = power if power is not None else load_power()

    def provider(self) -> DailyProvider:
        return self._provider

    def events(self, cfg: CongressConfig) -> list[InsiderEvent]:
        try:
            self._store.refresh()
        except Exception:  # noqa: BLE001 - a network hiccup degrades to whatever's already cached
            if not self._store.trades():
                # No prior successful refresh and nothing local to fall back
                # on — surfacing an honest error beats silently persisting
                # an empty run. Caught by the server's ValueError->400
                # handler.
                raise ValueError(
                    "congress data unavailable and no local store — retry later"
                ) from None
            _log.warning("congress: refresh failed — using existing store contents", exc_info=True)

        trading_days = self._provider.trading_days()
        if not trading_days:
            return []
        # Same window-floor rationale as RealInsiderBundle: without it, a
        # cluster whose latest filing predates trading_days[0] bisects to
        # index 0 and fires on day 1 — a stale-filing burst. 5 calendar days
        # of slack before the window start is plenty since clustering/
        # trigger logic only cares about relative order.
        min_filed = (date.fromisoformat(trading_days[0]) - timedelta(days=5)).isoformat()
        trades = self._store.trades(end=trading_days[-1])
        power = self.power if cfg.power_only else None
        raw = build_events(trades, cfg, trading_days, min_filed=min_filed, power=power)
        # No client: congress has no cap/news knobs and never calls
        # filter_events (deliberate — see module docstring), so enrichment
        # would burn 2 unthrottled Polygon calls per event for zero
        # consumers. Wire self._client back in if/when congress grows
        # cap/news conditioning.
        return enrich_events(raw, None, InsiderConfig())


def run_congress_strategy(strategy: Strategy, bundle: CongressBundle, risk_cfg: RiskConfig) -> InsiderResult:
    """Build CongressConfig from the strategy's `congress` dict (unknown keys
    dropped), pull events + a price feed from the bundle (which applies
    power-gating + enrichment internally), and run the SAME daily-bar
    simulator the insider strategy uses. The simulator's exit ladder only
    reads hold_days/stop_pct/trail_pct off its config, so those three shared
    fields are shimmed into an InsiderConfig for the run_insider call; the
    result's `config` is then swapped back to the real CongressConfig used,
    so callers (Lab UI, tests) see the strategy's actual knobs rather than
    that internal shim.
    """
    cfg = CongressConfig(**{
        k: v for k, v in strategy.congress.items() if k in CongressConfig.__dataclass_fields__
    })
    if not set(cfg.owners) <= _KNOWN_OWNERS:
        bad = sorted(set(cfg.owners) - _KNOWN_OWNERS)
        raise ValueError(f"unknown congress owners: {bad} (expected subset of {sorted(_KNOWN_OWNERS)})")
    if cfg.cluster_n < 1:
        raise ValueError(f"cfg.cluster_n must be >= 1, got {cfg.cluster_n}")

    events = bundle.events(cfg)
    exit_cfg = InsiderConfig(hold_days=cfg.hold_days, stop_pct=cfg.stop_pct, trail_pct=cfg.trail_pct)
    result = run_insider(
        events, bundle.provider(), exit_cfg, risk_cfg,
        max_concurrent=strategy.max_concurrent,
        max_gross_pct=strategy.max_gross_pct,
        slippage_pct=strategy.slippage_pct,
    )
    result.config = asdict(cfg)
    return result

