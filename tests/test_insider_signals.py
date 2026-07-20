"""Insider-buying signal construction: filters, clustering, routine detection."""
from __future__ import annotations

import itertools
from datetime import date, timedelta

from momentum_desk.insider.models import InsiderConfig, InsiderFiling
from momentum_desk.insider.signals import build_events, routine_keys

DAYS = [
    "2025-03-10", "2025-03-11", "2025-03-12", "2025-03-13", "2025-03-14",
    "2025-03-17", "2025-03-18", "2025-03-19", "2025-03-20", "2025-03-21",
]

_accession = itertools.count()


def mk(sym="ACME", filed="2025-03-10", code="P", shares=1000, price=50.0,
       owner="Jane Doe", **kw) -> InsiderFiling:
    return InsiderFiling(
        accession=f"acc-{next(_accession)}",
        symbol=sym,
        filed=filed,
        trans_date=kw.pop("trans_date", filed),
        code=code,
        shares=shares,
        price=price,
        owner_name=owner,
        **kw,
    )


def business_days(start: str, end: str) -> list[str]:
    """Test-only helper: weekday calendar dates from start to end inclusive."""
    d = date.fromisoformat(start)
    e = date.fromisoformat(end)
    out = []
    while d <= e:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_only_code_p_counts():
    for code in ["S", "A", "M", "F", "G"]:
        filings = [mk(code=code, is_officer=True)]
        cfg = InsiderConfig()
        assert build_events(filings, cfg, DAYS) == []


def test_min_value_filter():
    filings = [mk(shares=100, price=10.0, is_officer=True)]
    cfg = InsiderConfig()
    assert build_events(filings, cfg, DAYS) == []


def test_trigger_is_next_trading_day():
    filings = [mk(filed="2025-03-14", is_officer=True)]
    cfg = InsiderConfig()
    events = build_events(filings, cfg, DAYS)
    assert len(events) == 1
    assert events[0].trigger_day == "2025-03-17"


def test_filed_on_last_day_dropped():
    filings = [mk(filed="2025-03-21", is_officer=True)]
    cfg = InsiderConfig()
    assert build_events(filings, cfg, DAYS) == []


def test_cluster_requires_distinct_insiders():
    filings = [
        mk(filed="2025-03-10", owner="Jane Doe", is_officer=True),
        mk(filed="2025-03-12", owner="Jane Doe", is_officer=True),
    ]
    cfg = InsiderConfig(cluster_n=2)
    assert build_events(filings, cfg, DAYS) == []


def test_cluster_two_insiders_within_window():
    filings = [
        mk(filed="2025-03-10", owner="Jane Doe", is_officer=True),
        mk(filed="2025-03-15", owner="John Smith", is_officer=True),
    ]
    cfg = InsiderConfig(cluster_n=2)
    events = build_events(filings, cfg, DAYS)
    assert len(events) == 1
    assert events[0].n_insiders == 2


def test_cluster_outside_window_no_merge():
    ext_days = business_days("2025-03-10", "2025-03-28")
    filings = [
        mk(filed="2025-03-10", owner="Jane Doe", is_officer=True),
        mk(filed="2025-03-25", owner="John Smith", is_officer=True),
    ]
    cfg = InsiderConfig(cluster_n=1, cluster_window_days=10)
    events = build_events(filings, cfg, ext_days)
    assert len(events) == 2
    assert {e.n_insiders for e in events} == {1}


def test_role_filter_ceo_cfo():
    filings = [mk(is_director=True, is_officer=True)]
    cfg = InsiderConfig(roles="ceo_cfo")
    assert build_events(filings, cfg, DAYS) == []


def test_ten_pct_only_excluded_under_any():
    filings = [mk(is_ten_pct=True)]
    cfg = InsiderConfig(roles="any")
    assert build_events(filings, cfg, DAYS) == []


def test_10b51_excluded_by_default():
    filings = [mk(is_officer=True, tenb5_1=True)]
    cfg = InsiderConfig()
    assert build_events(filings, cfg, DAYS) == []


def test_routine_keys_same_month_three_years():
    filings_3y = [
        mk(filed="2022-03-05"),
        mk(filed="2023-03-12"),
        mk(filed="2024-03-20"),
    ]
    assert ("Jane Doe", "ACME") in routine_keys(filings_3y)

    filings_2y = [
        mk(filed="2022-03-05"),
        mk(filed="2023-03-12"),
    ]
    assert ("Jane Doe", "ACME") not in routine_keys(filings_2y)


def test_routine_filings_dropped():
    history = [
        mk(filed="2022-03-05", owner="Jane Doe", shares=10, price=1.0),
        mk(filed="2023-03-12", owner="Jane Doe", shares=10, price=1.0),
        mk(filed="2024-03-20", owner="Jane Doe", shares=10, price=1.0),
    ]
    candidate = [mk(filed="2025-03-10", owner="Jane Doe", is_officer=True)]
    cfg = InsiderConfig()
    assert build_events(history + candidate, cfg, DAYS) == []


def test_top_role_priority_and_conviction():
    filings = [
        mk(filed="2025-03-10", owner="Jane Doe", shares=1000, price=20.0,
           is_director=True, is_officer=True, shares_owned_after=500),
        mk(filed="2025-03-12", owner="John Smith", shares=500, price=20.0,
           is_ceo=True, is_officer=True, shares_owned_after=1000),
    ]
    cfg = InsiderConfig()
    events = build_events(filings, cfg, DAYS)
    assert len(events) == 1
    ev = events[0]
    assert ev.top_role == "ceo"
    assert ev.total_value == 30_000.0
    assert ev.conviction == 0.5


def test_min_filed_drops_stale_cluster_keeps_in_window_one():
    """RealInsiderBundle review finding: without a floor, a cluster whose
    latest filing predates trading_days[0] gets bisect_right == 0 and fires
    on day 1 regardless of how stale it is. min_filed drops those while an
    in-window cluster still emits normally."""
    filings = [
        mk(sym="STALE", filed="2025-02-01", is_officer=True),   # predates DAYS[0]
        mk(sym="ACME", filed="2025-03-14", is_officer=True),    # within DAYS
    ]
    cfg = InsiderConfig()
    events = build_events(filings, cfg, DAYS, min_filed="2025-03-01")
    assert [e.symbol for e in events] == ["ACME"]


def test_min_filed_none_default_unchanged_behavior():
    filings = [mk(filed="2025-03-14", is_officer=True)]
    cfg = InsiderConfig()
    events = build_events(filings, cfg, DAYS)
    assert len(events) == 1
    assert events[0].trigger_day == "2025-03-17"


def test_min_filed_does_not_affect_routine_keys_lookback():
    """routine_keys must still see the FULL filings list even when min_filed
    would exclude the routine history's own trigger — min_filed only gates
    which clusters can EMIT, not what routine_keys is computed from."""
    history = [
        mk(filed="2022-03-05", owner="Jane Doe", shares=10, price=1.0),
        mk(filed="2023-03-12", owner="Jane Doe", shares=10, price=1.0),
        mk(filed="2024-03-20", owner="Jane Doe", shares=10, price=1.0),
    ]
    candidate = [mk(filed="2025-03-10", owner="Jane Doe", is_officer=True)]
    cfg = InsiderConfig()
    events = build_events(history + candidate, cfg, DAYS, min_filed="2025-01-01")
    assert events == []   # still dropped as a routine trader, not emitted via min_filed gap


def test_events_sorted_by_trigger_day():
    filings = [
        mk(sym="ACME", filed="2025-03-18", owner="Jane Doe", is_officer=True),
        mk(sym="ZETA", filed="2025-03-10", owner="John Smith", is_officer=True),
    ]
    cfg = InsiderConfig()
    events = build_events(filings, cfg, DAYS)
    assert [e.symbol for e in events] == ["ZETA", "ACME"]
