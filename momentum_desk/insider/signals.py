"""Turn raw Form 4 line items into tradable insider-buying events.

Everything here is a pure function over plain lists — no I/O, no network,
no dependency on how the filings were fetched (that's Task 2's EDGAR
loader) or on market data (Task 4 enriches events with market cap/news;
the simulator in later tasks consumes ``InsiderEvent`` directly). Keeping
this layer pure means the clustering/routine-trader/role logic can be
unit-tested against synthetic filings without a network or a database,
and reused unchanged once the real loader exists.
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from datetime import date

from .models import InsiderConfig, InsiderEvent, InsiderFiling

_ROLE_RANK = {"ceo": 0, "cfo": 1, "officer": 2, "director": 3, "10pct": 4}


def routine_keys(filings: list[InsiderFiling]) -> set[tuple[str, str]]:
    """(owner_name, symbol) pairs whose code-P buys hit the same calendar month
    in >= 3 distinct years — the routine-trader filter."""
    years_by_month: dict[tuple[str, str], dict[int, set[int]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for f in filings:
        if f.code != "P":
            continue
        d = date.fromisoformat(f.filed)
        years_by_month[(f.owner_name, f.symbol)][d.month].add(d.year)

    return {
        key
        for key, months in years_by_month.items()
        if any(len(years) >= 3 for years in months.values())
    }


def _role_pass(f: InsiderFiling, roles: str) -> bool:
    if roles == "ceo_cfo":
        return f.is_ceo or f.is_cfo
    if roles == "officer":
        return f.is_officer or f.is_director
    if roles == "any":
        pure_ten_pct = f.is_ten_pct and not (
            f.is_ceo or f.is_cfo or f.is_officer or f.is_director
        )
        return not pure_ten_pct
    raise ValueError(f"unknown InsiderConfig.roles: {roles!r}")


def _top_role(cluster: list[InsiderFiling]) -> str:
    present = set()
    for f in cluster:
        if f.is_ceo:
            present.add("ceo")
        if f.is_cfo:
            present.add("cfo")
        if f.is_officer:
            present.add("officer")
        if f.is_director:
            present.add("director")
        if f.is_ten_pct:
            present.add("10pct")
    if not present:
        return ""
    return min(present, key=lambda role: _ROLE_RANK[role])


def build_events(
    filings: list[InsiderFiling], cfg: InsiderConfig, trading_days: list[str],
    *, min_filed: str | None = None,
) -> list[InsiderEvent]:
    """Pure function: apply filters, cluster per symbol, emit events sorted by
    trigger_day. Only days in `trading_days` are eligible trigger days; the
    trigger is the first trading day STRICTLY AFTER the latest filing date in
    the cluster. Filings whose trigger would fall past the last trading day
    are dropped. Enrichment fields are left at defaults (Task 4 fills them).

    `min_filed`, when set, drops any cluster whose latest `filed` predates it
    — otherwise a cluster that's stale relative to the trading window (its
    latest filing predates trading_days[0]) gets `bisect_right` == 0 and all
    pile onto trigger_day == trading_days[0], a day-1 burst of ancient
    filings. routine_keys is still computed from the FULL, unfiltered
    `filings` list — the routine-trader lookback needs the long history."""
    routine = routine_keys(filings) if cfg.exclude_routine else set()

    kept = [f for f in filings if f.code == "P" and f.shares > 0 and f.price > 0]
    if cfg.exclude_10b51:
        kept = [f for f in kept if not f.tenb5_1]
    kept = [f for f in kept if _role_pass(f, cfg.roles)]
    if cfg.exclude_routine:
        kept = [f for f in kept if (f.owner_name, f.symbol) not in routine]

    by_symbol: dict[str, list[InsiderFiling]] = defaultdict(list)
    for f in kept:
        by_symbol[f.symbol].append(f)

    events: list[InsiderEvent] = []
    for symbol, symbol_filings in by_symbol.items():
        ordered = sorted(symbol_filings, key=lambda f: f.filed)
        i = 0
        n = len(ordered)
        while i < n:
            window_end = date.fromisoformat(ordered[i].filed).toordinal() + cfg.cluster_window_days
            j = i
            while j < n and date.fromisoformat(ordered[j].filed).toordinal() <= window_end:
                j += 1
            cluster = ordered[i:j]
            i = j  # consume — no overlapping re-emission

            owners = {f.owner_name for f in cluster}
            total_value = sum(f.value for f in cluster)
            if len(owners) < cfg.cluster_n or total_value < cfg.min_value:
                continue

            latest_filed = max(f.filed for f in cluster)
            if min_filed is not None and latest_filed < min_filed:
                continue
            trigger_idx = bisect.bisect_right(trading_days, latest_filed)
            if trigger_idx >= len(trading_days):
                continue  # no future trading day left — drop
            trigger_day = trading_days[trigger_idx]

            holdings_value = sum(f.shares_owned_after * f.price for f in cluster)
            denom = total_value + holdings_value
            conviction = total_value / denom if denom > 0 else 1.0

            events.append(
                InsiderEvent(
                    symbol=symbol,
                    trigger_day=trigger_day,
                    total_value=total_value,
                    n_insiders=len(owners),
                    top_role=_top_role(cluster),
                    conviction=conviction,
                )
            )

    events.sort(key=lambda e: e.trigger_day)
    return events
