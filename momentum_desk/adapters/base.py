"""Data-feed contract. Anything that can emit `Snapshot`s is a valid source:
the mock replay feed today, polygon.io / Finnhub / IBKR tomorrow. The scanner
never knows or cares which one is attached.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from ..models import Snapshot


@runtime_checkable
class MarketDataAdapter(Protocol):
    """Minimal surface a market-data source must implement."""

    name: str

    def universe(self) -> list[str]:
        """Symbols this feed currently tracks."""
        ...

    def poll(self) -> Iterable[Snapshot]:
        """Return the latest snapshot for every tracked symbol.

        Called on each scan tick. Implementations should be non-blocking and
        return fast — the dashboard's responsiveness depends on it.
        """
        ...
