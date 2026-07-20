"""Turn raw congress-trading disclosures into tradable buy events.

Mirrors ``momentum_desk/insider/signals.py``'s filter -> cluster -> trigger
pipeline (see that module's docstring for the design rationale) but keyed
on kadoa ``CongressTrade`` fields instead of Form 4 ``InsiderFiling``
fields, and produces the SAME ``InsiderEvent`` shape so
``insider.simulate.run_insider`` runs against congress signals unchanged.
The small cluster/trigger helpers below are copied rather than imported —
``insider.signals``'s equivalents are private module internals, not a
public contract, and the two strategies' filter semantics (stock-only
asset type, power-member gate) diverge enough that sharing code would
just add indirection for little reuse.
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from ..insider.models import InsiderEvent
from .loader import CongressTrade

# Task 1's real-data probe (see congress-task-1-report.md) found the kadoa
# dataset uses two incompatible asset_type vocabularies split by chamber:
# House rows use abbreviated codes ("ST" = single stock), Senate rows spell
# it out ("Stock"). A blank/missing asset_type ("") is treated as stock per
# the design brief. Everything else observed (options "OP", "OT"/"Other",
# "OL", bonds, crypto, non-public stock) is dropped — the spec wants
# single-stock assets only (funds/ETFs/options excluded in v1). Compared
# case-insensitively so a stray lowercase variant still matches.
_STOCK_ASSET_TYPES = {"ST", "Stock", ""}
_STOCK_ASSET_TYPES_LOWER = {s.lower() for s in _STOCK_ASSET_TYPES}


@dataclass
class CongressConfig:
    """Filters and clustering/exit knobs for the congress-trading strategy."""

    min_amount: float = 15_001.0          # drop the modal $1,001-15,000 disclosure bracket
    owners: tuple[str, ...] = ("SELF", "SP", "JT")   # exclude dependents (DC) by default
    power_only: bool = False              # require filer_id in the curated power set
    cluster_n: int = 1                    # min distinct filers buying within the window
    cluster_window_days: int = 30         # calendar-day window for clustering, on filing_date
    max_days_to_file: int = 45            # drop stale/late-filer noise
    hold_days: int = 21                   # time stop, trading days
    stop_pct: float = 20.0                # hard stop below entry
    trail_pct: float = 25.0               # trailing stop from highest close since entry


def _is_stock(asset_type: str) -> bool:
    return asset_type.strip().lower() in _STOCK_ASSET_TYPES_LOWER


def build_events(
    trades: list[CongressTrade], cfg: CongressConfig, trading_days: list[str],
    *, min_filed: str | None = None, power: set[str] | None = None,
) -> list[InsiderEvent]:
    """Pure function: apply filters (in the order the design brief lists
    them), cluster per ticker on FILING dates, emit events sorted by
    trigger_day. Only days in `trading_days` are eligible trigger days; the
    trigger is the first trading day STRICTLY AFTER the latest filing_date
    in the cluster — trading on transaction_date would be lookahead (the
    STOCK Act allows up to 45 days between transaction and disclosure).
    Clusters whose trigger would fall past the last trading day are
    dropped, as are clusters whose latest filing_date predates `min_filed`
    (same stale-cluster guard as insider.signals.build_events — without it
    a cluster older than the trading window bisects to index 0 and fires
    on day 1 regardless of staleness).

    `power_only=True` requires a non-None `power` set (ValueError
    otherwise) — a silently-empty power gate would make the strategy trade
    nothing without any signal that something's misconfigured.
    """
    if cfg.power_only and power is None:
        raise ValueError("cfg.power_only requires a non-None `power` set")

    kept = [t for t in trades if t.transaction_type == "Purchase"]
    kept = [t for t in kept if _is_stock(t.asset_type)]
    kept = [t for t in kept if t.owner in cfg.owners]
    kept = [t for t in kept if t.amount_low >= cfg.min_amount]
    kept = [t for t in kept if t.days_to_file <= cfg.max_days_to_file]
    if cfg.power_only:
        kept = [t for t in kept if t.filer_id in power]

    by_symbol: dict[str, list[CongressTrade]] = defaultdict(list)
    for t in kept:
        by_symbol[t.ticker].append(t)

    events: list[InsiderEvent] = []
    for symbol, symbol_trades in by_symbol.items():
        ordered = sorted(symbol_trades, key=lambda t: t.filing_date)
        i = 0
        n = len(ordered)
        while i < n:
            window_end = date.fromisoformat(ordered[i].filing_date).toordinal() + cfg.cluster_window_days
            j = i
            while j < n and date.fromisoformat(ordered[j].filing_date).toordinal() <= window_end:
                j += 1
            cluster = ordered[i:j]
            i = j  # consume — no overlapping re-emission

            filers = {t.filer_id for t in cluster}
            if len(filers) < cfg.cluster_n:
                continue

            latest_filed = max(t.filing_date for t in cluster)
            if min_filed is not None and latest_filed < min_filed:
                continue
            trigger_idx = bisect.bisect_right(trading_days, latest_filed)
            if trigger_idx >= len(trading_days):
                continue  # no future trading day left — drop
            trigger_day = trading_days[trigger_idx]

            total_value = sum((t.amount_low + t.amount_high) / 2 for t in cluster)
            top_role = "power" if power and (filers & power) else "member"

            events.append(
                InsiderEvent(
                    symbol=symbol,
                    trigger_day=trigger_day,
                    total_value=total_value,
                    n_insiders=len(filers),
                    top_role=top_role,
                    conviction=0.5,
                )
            )

    events.sort(key=lambda e: e.trigger_day)
    return events
