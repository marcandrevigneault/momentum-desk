"""RealInsiderBundle hardening: unpublished-quarter safety and the
window-floor against stale-filing bursts. Uses duck-typed fakes for the
EdgarStore/provider/client seams (RealInsiderBundle only calls a handful of
methods on each) so these run with no network and no real EDGAR/Polygon
access."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from momentum_desk.insider.bundles import RealInsiderBundle
from momentum_desk.insider.models import InsiderConfig, InsiderFiling


@dataclass
class FakeProvider:
    days: list[str]

    def trading_days(self) -> list[str]:
        return list(self.days)

    def daily(self, symbol: str):
        return []


@dataclass
class FakeStore:
    """Records every (year, quarter) requested; raises for quarters listed
    in `raises_for`; returns canned filings from `filings()`."""
    raises_for: set[tuple[int, int]] = field(default_factory=set)
    loaded: list[tuple[int, int]] = field(default_factory=list)
    _filings: list[InsiderFiling] = field(default_factory=list)

    def load_quarter(self, year: int, quarter: int) -> int:
        self.loaded.append((year, quarter))
        if (year, quarter) in self.raises_for:
            raise RuntimeError(f"could not fetch {year}q{quarter} form345 zip")
        return 0

    def filings(self, *, start: str | None = None, end: str | None = None) -> list[InsiderFiling]:
        return list(self._filings)


class FakeClient:
    """Stands in for CachedClient: every call fails, so enrich_events leaves
    fields at their defaults — exercised path only, no real network."""

    def get_json(self, *args, **kwargs):
        raise RuntimeError("no network in tests")


def _mk(sym="ACME", filed="2025-03-10", owner="Jane Doe", **kw) -> InsiderFiling:
    return InsiderFiling(
        accession=f"acc-{filed}-{owner}", symbol=sym, filed=filed, trans_date=filed,
        code="P", shares=1000, price=50.0, owner_name=owner, is_officer=True, **kw,
    )


def test_missing_quarter_logs_and_skips_not_crashes(caplog):
    days = ["2025-03-10", "2025-03-11", "2025-03-12"]
    store = FakeStore(raises_for={(2025, 1)})
    store._filings = [_mk(filed="2025-03-05")]   # loaded from the (surviving) quarter
    bundle = RealInsiderBundle(
        api_key="x", store=store, provider=FakeProvider(days=days),
        client=FakeClient(), lookback_years=1,
    )

    import logging
    with caplog.at_level(logging.WARNING):
        events = bundle.events(InsiderConfig())

    # the run must not raise, and must still surface the loaded quarter's events
    assert isinstance(events, list)
    assert "2025q1" in caplog.text.lower()
    assert (2025, 1) in store.loaded   # the failing quarter was still attempted


def test_current_in_progress_quarter_never_requested(monkeypatch):
    """SEC only publishes a quarter's zip after it closes — the quarter
    containing `date.today()` must never be requested."""
    fake_today = date(2025, 5, 15)   # mid Q2 2025 -> Q2 2025 must be skipped

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return fake_today

    import momentum_desk.insider.bundles as bundles_module
    monkeypatch.setattr(bundles_module, "date", _FixedDate)

    days = ["2025-04-01", "2025-04-02"]
    store = FakeStore()
    bundle = RealInsiderBundle(
        api_key="x", store=store, provider=FakeProvider(days=days),
        client=FakeClient(), lookback_years=1,
    )
    bundle.events(InsiderConfig())

    assert (2025, 2) not in store.loaded    # current quarter: skipped
    assert (2025, 1) in store.loaded        # a fully-closed prior quarter: requested


def test_min_filed_wired_from_price_window_start():
    """RealInsiderBundle must pass min_filed = 5 calendar days before the
    price window's first trading day, so a stale cluster (filed long before
    the window) does not fire on day 1 while an in-window one still does."""
    days = ["2025-03-10", "2025-03-11", "2025-03-12", "2025-03-13"]
    store = FakeStore()
    store._filings = [
        _mk(sym="STALE", filed="2025-01-01"),   # long before the window: must be dropped
        _mk(sym="ACME", filed="2025-03-11", owner="John Smith"),  # in-window: must fire
    ]
    bundle = RealInsiderBundle(
        api_key="x", store=store, provider=FakeProvider(days=days),
        client=FakeClient(), lookback_years=1,
    )
    events = bundle.events(InsiderConfig())

    symbols = {e.symbol for e in events}
    assert "STALE" not in symbols
    assert "ACME" in symbols
