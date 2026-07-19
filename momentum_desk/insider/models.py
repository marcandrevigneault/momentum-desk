"""Data contracts for the insider-buying strategy.

The Lab's other strategies react to price/volume; this one reacts to a
public disclosure — a Form 4 filing showing a company insider bought stock
with their own money. ``InsiderFiling`` is one line item from that filing
(one insider, one transaction). ``InsiderEvent`` is what the strategy
actually trades: a cluster of qualifying filings for a symbol, rolled up
into a single tradable signal on its trigger day. ``InsiderConfig`` is the
knob set that turns raw filings into events (Task 1) and later drives
entries/exits in the simulator (Task 5+) — it lives here, next to the
things it configures, rather than in the shared ``RiskConfig``, because
none of these knobs mean anything outside this strategy.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class InsiderFiling:
    """One insider's reported transaction from a single Form 4 filing."""

    accession: str
    symbol: str
    filed: str            # YYYY-MM-DD (EDGAR filing date)
    trans_date: str       # YYYY-MM-DD
    code: str             # SEC transaction code: P, S, A, M, F, G, ...
    shares: float
    price: float
    owner_name: str
    is_ceo: bool = False
    is_cfo: bool = False
    is_officer: bool = False
    is_director: bool = False
    is_ten_pct: bool = False
    officer_title: str = ""
    tenb5_1: bool = False          # 10b5-1 plan flag (False when unknown/pre-2023)
    shares_owned_after: float = 0.0

    @property
    def value(self) -> float:      # dollar value of the transaction
        return self.shares * self.price


@dataclass
class InsiderEvent:
    """A tradable signal: one or more clustered insider buys in a symbol."""

    symbol: str
    trigger_day: str               # first tradable day (next trading day after max filed date)
    total_value: float
    n_insiders: int
    top_role: str                  # "ceo" | "cfo" | "officer" | "director" | "10pct"
    conviction: float               # total bought value / (value + holdings-after value), 0..1
    market_cap: float | None = None
    sector: str | None = None
    has_recent_news: bool = False
    news_headline: str = ""


@dataclass
class InsiderConfig:
    """Filters and clustering/exit knobs for the insider-buying strategy."""

    min_value: float = 25_000.0
    roles: str = "officer"         # "any" | "officer" (any officer or director) | "ceo_cfo"
    cluster_n: int = 1             # min distinct insiders buying within the window
    cluster_window_days: int = 10  # calendar-day window for clustering
    exclude_10b51: bool = True
    exclude_routine: bool = True   # Cohen-Malloy-Pomorski same-calendar-month heuristic
    max_market_cap: float | None = None   # dollars; None = no cap; None market_cap passes unless include_unknown=False
    include_unknown_cap: bool = True
    news_filter: str = "any"       # "any" | "quiet" (no news in lookback) | "with_news"
    news_lookback_days: int = 5
    hold_days: int = 20            # time stop, trading days
    stop_pct: float = 20.0         # hard stop below entry
    trail_pct: float = 15.0        # trailing stop from highest close since entry
