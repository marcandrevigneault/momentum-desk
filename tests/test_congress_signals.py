"""Congress-trading signal construction: filters, clustering, power list.

Mirrors tests/test_insider_signals.py's structure — same greedy per-symbol
cluster-on-filing-dates algorithm, same strictly-after trigger, same
min_filed floor — but keyed on CongressTrade fields (owner/asset_type/
transaction_type/amount_low/days_to_file/filer_id) instead of InsiderFiling
fields, plus the extra power_only/power gate that has no insider analogue.
"""
from __future__ import annotations

import itertools

import pytest

from momentum_desk.congress.loader import CongressTrade
from momentum_desk.congress.power import load_power
from momentum_desk.congress.signals import CongressConfig, build_events

DAYS = [
    "2025-03-10", "2025-03-11", "2025-03-12", "2025-03-13", "2025-03-14",
    "2025-03-17", "2025-03-18", "2025-03-19", "2025-03-20", "2025-03-21",
]

_seq = itertools.count()


def mk(sym="ACME", filing_date="2025-03-10", transaction_date=None, owner="SELF",
       asset_type="ST", transaction_type="Purchase", amount_low=20_000.0,
       amount_high=30_000.0, days_to_file=10, filer_id=None, **kw) -> CongressTrade:
    n = next(_seq)
    return CongressTrade(
        filer_id=filer_id or f"house_member_{n}",
        member_name=kw.pop("member_name", f"Member {n}"),
        chamber=kw.pop("chamber", "house"),
        ticker=sym,
        transaction_date=transaction_date or filing_date,
        filing_date=filing_date,
        owner=owner,
        asset_type=asset_type,
        transaction_type=transaction_type,
        amount_low=amount_low,
        amount_high=amount_high,
        is_late=kw.pop("is_late", False),
        days_to_file=days_to_file,
        **kw,
    )


# --- rule filters ------------------------------------------------------


def test_only_purchase_transaction_type_kept():
    for ttype in ["Sale (Partial)", "Sale (Full)", "Exchange", ""]:
        trades = [mk(transaction_type=ttype)]
        assert build_events(trades, CongressConfig(), DAYS) == []


def test_asset_type_stock_only_accepts_both_vocabularies():
    for asset in ["ST", "Stock", ""]:
        trades = [mk(asset_type=asset)]
        events = build_events(trades, CongressConfig(), DAYS)
        assert len(events) == 1, f"asset_type={asset!r} should pass the stock filter"


def test_asset_type_non_stock_dropped():
    for asset in ["OP", "OT", "Other", "Non-Public Stock", "Corporate Bond", "Cryptocurrency", "OL"]:
        trades = [mk(asset_type=asset)]
        assert build_events(trades, CongressConfig(), DAYS) == [], f"asset_type={asset!r} should be dropped"


def test_owner_filter_default_excludes_dependent_child():
    trades = [mk(owner="DC")]
    assert build_events(trades, CongressConfig(), DAYS) == []


def test_owner_filter_allows_self_sp_jt_by_default():
    for owner in ["SELF", "SP", "JT"]:
        trades = [mk(owner=owner)]
        events = build_events(trades, CongressConfig(), DAYS)
        assert len(events) == 1, f"owner={owner!r} should pass"


def test_owner_filter_respects_custom_owners_tuple():
    trades = [mk(owner="SP")]
    cfg = CongressConfig(owners=("SELF",))
    assert build_events(trades, cfg, DAYS) == []


def test_min_amount_filter():
    trades = [mk(amount_low=1_000.0, amount_high=15_000.0)]
    assert build_events(trades, CongressConfig(), DAYS) == []


def test_min_amount_filter_boundary_passes():
    trades = [mk(amount_low=15_001.0, amount_high=50_000.0)]
    events = build_events(trades, CongressConfig(), DAYS)
    assert len(events) == 1


def test_max_days_to_file_filter():
    trades = [mk(days_to_file=46)]
    assert build_events(trades, CongressConfig(), DAYS) == []


def test_max_days_to_file_boundary_passes():
    trades = [mk(days_to_file=45)]
    events = build_events(trades, CongressConfig(), DAYS)
    assert len(events) == 1


# --- power filter --------------------------------------------------------


def test_power_only_without_power_set_raises_valueerror():
    trades = [mk(filer_id="house_jane_powerful")]
    cfg = CongressConfig(power_only=True)
    with pytest.raises(ValueError):
        build_events(trades, cfg, DAYS, power=None)


def test_power_only_keeps_only_power_filers():
    trades = [
        mk(sym="ACME", filer_id="house_jane_powerful"),
        mk(sym="ZETA", filer_id="house_john_backbencher"),
    ]
    cfg = CongressConfig(power_only=True)
    events = build_events(trades, cfg, DAYS, power={"house_jane_powerful"})
    assert [e.symbol for e in events] == ["ACME"]


def test_top_role_power_when_clustered_filer_in_power_set():
    trades = [mk(filer_id="house_jane_powerful")]
    events = build_events(trades, CongressConfig(), DAYS, power={"house_jane_powerful"})
    assert events[0].top_role == "power"


def test_top_role_member_when_no_power_set_given():
    trades = [mk(filer_id="house_jane_powerful")]
    events = build_events(trades, CongressConfig(), DAYS)
    assert events[0].top_role == "member"


def test_top_role_member_when_filer_not_in_power_set():
    trades = [mk(filer_id="house_john_backbencher")]
    events = build_events(trades, CongressConfig(), DAYS, power={"house_jane_powerful"})
    assert events[0].top_role == "member"


# --- clustering ------------------------------------------------------


def test_cluster_requires_distinct_filers():
    trades = [
        mk(filing_date="2025-03-10", filer_id="house_jane"),
        mk(filing_date="2025-03-12", filer_id="house_jane"),
    ]
    cfg = CongressConfig(cluster_n=2)
    assert build_events(trades, cfg, DAYS) == []


def test_cluster_two_distinct_filers_within_window():
    trades = [
        mk(filing_date="2025-03-10", filer_id="house_jane"),
        mk(filing_date="2025-03-15", filer_id="house_john"),
    ]
    cfg = CongressConfig(cluster_n=2)
    events = build_events(trades, cfg, DAYS)
    assert len(events) == 1
    assert events[0].n_insiders == 2


def test_cluster_outside_window_no_merge():
    ext_days = DAYS + ["2025-03-24", "2025-03-25", "2025-03-26", "2025-03-27", "2025-03-28"]
    trades = [
        mk(filing_date="2025-03-10", filer_id="house_jane"),
        mk(filing_date="2025-03-25", filer_id="house_john"),
    ]
    cfg = CongressConfig(cluster_n=1, cluster_window_days=10)
    events = build_events(trades, cfg, ext_days)
    assert len(events) == 2
    assert {e.n_insiders for e in events} == {1}


def test_total_value_is_sum_of_amount_midpoints():
    trades = [
        mk(filing_date="2025-03-10", filer_id="house_jane", amount_low=20_000.0, amount_high=30_000.0),
        mk(filing_date="2025-03-12", filer_id="house_john", amount_low=50_000.0, amount_high=100_000.0),
    ]
    cfg = CongressConfig(cluster_n=2)
    events = build_events(trades, cfg, DAYS)
    assert len(events) == 1
    assert events[0].total_value == 25_000.0 + 75_000.0


def test_conviction_fixed_at_half():
    trades = [mk()]
    events = build_events(trades, CongressConfig(), DAYS)
    assert events[0].conviction == 0.5


# --- trigger / min_filed -------------------------------------------------


def test_trigger_is_next_trading_day_strictly_after_filing():
    trades = [mk(filing_date="2025-03-14")]
    events = build_events(trades, CongressConfig(), DAYS)
    assert len(events) == 1
    assert events[0].trigger_day == "2025-03-17"


def test_filed_on_last_trading_day_dropped():
    trades = [mk(filing_date="2025-03-21")]
    assert build_events(trades, CongressConfig(), DAYS) == []


def test_min_filed_drops_stale_cluster():
    trades = [
        mk(sym="STALE", filing_date="2025-02-01"),
        mk(sym="ACME", filing_date="2025-03-14"),
    ]
    events = build_events(trades, CongressConfig(), DAYS, min_filed="2025-03-01")
    assert [e.symbol for e in events] == ["ACME"]


def test_min_filed_none_default_unchanged_behavior():
    trades = [mk(filing_date="2025-03-14")]
    events = build_events(trades, CongressConfig(), DAYS)
    assert len(events) == 1
    assert events[0].trigger_day == "2025-03-17"


# --- ordering --------------------------------------------------------


def test_events_sorted_by_trigger_day():
    trades = [
        mk(sym="ACME", filing_date="2025-03-18", filer_id="house_a"),
        mk(sym="ZETA", filing_date="2025-03-10", filer_id="house_b"),
    ]
    events = build_events(trades, CongressConfig(), DAYS)
    assert [e.symbol for e in events] == ["ZETA", "ACME"]


# --- power.json / load_power ------------------------------------------


def test_load_power_valid_schema_flattens_to_set(tmp_path):
    p = tmp_path / "power.json"
    p.write_text(
        '{"congresses": {"118": {"house": ["house_a", "house_b"], "senate": ["senate_a"]}, '
        '"119": {"house": ["house_a"], "senate": ["senate_a", "senate_c"]}}, '
        '"sources": ["https://example.com"]}'
    )
    power = load_power(str(p))
    assert power == {"house_a", "house_b", "senate_a", "senate_c"}


def test_load_power_missing_congresses_key_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{"sources": []}')
    with pytest.raises(ValueError):
        load_power(str(p))


def test_load_power_non_dict_chamber_value_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{"congresses": {"118": {"house": "not-a-list", "senate": []}}}')
    with pytest.raises(ValueError):
        load_power(str(p))


def test_load_power_default_path_returns_non_empty_set_with_speaker():
    power = load_power()
    assert len(power) > 0
    assert "house_mike_johnson" in power
