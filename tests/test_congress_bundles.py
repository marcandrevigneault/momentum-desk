"""RealCongressBundle hardening: refresh-failure degradation and the
window-floor against stale-filing bursts. Uses duck-typed fakes for the
CongressStore/provider/client seams (RealCongressBundle only calls a handful
of methods on each) so these run with no network and no real GitHub/Polygon
access. Mirrors tests/test_insider_bundles.py's structure."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from momentum_desk.congress.bundles import RealCongressBundle, run_congress_strategy
from momentum_desk.congress.loader import CongressTrade
from momentum_desk.congress.signals import CongressConfig
from momentum_desk.edge.strategy import Strategy
from momentum_desk.risk import RiskConfig


@dataclass
class FakeProvider:
    days: list[str]

    def trading_days(self) -> list[str]:
        return list(self.days)

    def daily(self, symbol: str):
        return []


@dataclass
class FakeStore:
    """Records whether refresh() was called; raises if `raise_on_refresh` is
    set; returns canned trades from `trades()`."""
    raise_on_refresh: bool = False
    refreshed: bool = False
    _trades: list[CongressTrade] = field(default_factory=list)

    def refresh(self) -> int:
        self.refreshed = True
        if self.raise_on_refresh:
            raise RuntimeError("network is down")
        return 0

    def trades(self, *, start: str | None = None, end: str | None = None) -> list[CongressTrade]:
        return list(self._trades)


class FakeClient:
    """Stands in for CachedClient: every call fails, so enrich_events leaves
    fields at their defaults — exercised path only, no real network."""

    def get_json(self, *args, **kwargs):
        raise RuntimeError("no network in tests")


def _mk(sym="ACME", filing_date="2025-03-10", filer_id="house_jane_doe", **kw) -> CongressTrade:
    return CongressTrade(
        filer_id=filer_id, member_name=kw.pop("member_name", "Jane Doe"),
        chamber=kw.pop("chamber", "house"), ticker=sym,
        transaction_date=kw.pop("transaction_date", filing_date), filing_date=filing_date,
        owner=kw.pop("owner", "SELF"), asset_type=kw.pop("asset_type", "ST"),
        transaction_type=kw.pop("transaction_type", "Purchase"),
        amount_low=kw.pop("amount_low", 20_000.0), amount_high=kw.pop("amount_high", 30_000.0),
        is_late=kw.pop("is_late", False), days_to_file=kw.pop("days_to_file", 10),
    )


def test_refresh_failure_logs_and_degrades_to_existing_store(caplog):
    days = ["2025-03-10", "2025-03-11", "2025-03-12"]
    store = FakeStore(raise_on_refresh=True)
    store._trades = [_mk(filing_date="2025-03-10")]
    bundle = RealCongressBundle(
        api_key="x", store=store, provider=FakeProvider(days=days),
        client=FakeClient(), power=set(),
    )

    import logging
    with caplog.at_level(logging.WARNING):
        events = bundle.events(CongressConfig())

    # the run must not raise, refresh() must still have been attempted, and
    # whatever the store already had must still surface as events
    assert isinstance(events, list)
    assert len(events) >= 1
    assert store.refreshed


def test_refresh_failure_with_no_local_data_raises_valueerror():
    """A cold store (no prior successful refresh, nothing cached) whose
    refresh() also fails has nothing honest to serve — it must raise
    ValueError rather than silently returning an empty (fake-looking)
    result. Caught by the server's ValueError->400 handler."""
    days = ["2025-03-10", "2025-03-11", "2025-03-12"]
    store = FakeStore(raise_on_refresh=True)  # trades() stays empty
    bundle = RealCongressBundle(
        api_key="x", store=store, provider=FakeProvider(days=days),
        client=FakeClient(), power=set(),
    )

    with pytest.raises(ValueError, match="congress data unavailable"):
        bundle.events(CongressConfig())
    assert store.refreshed


def test_refresh_failure_with_no_local_data_propagates_through_run_congress_strategy():
    days = ["2025-03-10", "2025-03-11", "2025-03-12"]
    store = FakeStore(raise_on_refresh=True)
    bundle = RealCongressBundle(
        api_key="x", store=store, provider=FakeProvider(days=days),
        client=FakeClient(), power=set(),
    )
    s = Strategy(name="cong", kind="congress")

    with pytest.raises(ValueError, match="congress data unavailable"):
        run_congress_strategy(s, bundle, RiskConfig())


def test_min_filed_wired_from_price_window_start():
    """RealCongressBundle must pass min_filed = 5 calendar days before the
    price window's first trading day, so a stale trade (filed long before
    the window) does not fire on day 1 while an in-window one still does."""
    days = ["2025-03-10", "2025-03-11", "2025-03-12", "2025-03-13"]
    store = FakeStore()
    store._trades = [
        _mk(sym="STALE", filing_date="2025-01-01", filer_id="house_stale"),   # long before the window
        _mk(sym="ACME", filing_date="2025-03-11", filer_id="house_fresh"),    # in-window: must fire
    ]
    bundle = RealCongressBundle(
        api_key="x", store=store, provider=FakeProvider(days=days),
        client=FakeClient(), power=set(),
    )
    events = bundle.events(CongressConfig())

    symbols = {e.symbol for e in events}
    assert "STALE" not in symbols
    assert "ACME" in symbols


def test_power_only_uses_loaded_power_set():
    days = ["2025-03-10", "2025-03-11", "2025-03-12"]
    store = FakeStore()
    store._trades = [
        _mk(sym="ACME", filer_id="house_powerful", filing_date="2025-03-10"),
        _mk(sym="ZETA", filer_id="house_backbencher", filing_date="2025-03-10"),
    ]
    bundle = RealCongressBundle(
        api_key="x", store=store, provider=FakeProvider(days=days),
        client=FakeClient(), power={"house_powerful"},
    )
    events = bundle.events(CongressConfig(power_only=True))

    assert [e.symbol for e in events] == ["ACME"]
