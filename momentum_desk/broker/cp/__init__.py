"""IBKR Client Portal (CP) Web API integration — the bravos-style flow.

Unlike the socket-based ``ib_async`` adapter in ``broker/ibkr.py`` (which talks
to a running TWS / IB Gateway desktop app), this package drives the **IBKR
Client Portal Gateway**: a small local REST server on ``localhost:5000``.

The win is the login experience. The gateway is auto-started and auto-filled by
``ibeam`` (a headless-Chromium helper baked into the container); the only manual
step left is approving the **IBKR Mobile push (IB Key 2FA) on your phone** — one
tap. After that the session lasts ~24h and a ``/tickle`` keepalive holds it open.

Modules:
  - ``client``  — async httpx wrapper over the CP Web API (auth, portfolio, orders)
  - ``gateway`` — auth-status health snapshot, ``wait_for_auth``, keepalive loop
  - ``orders``  — pure order-payload builders (MKT / STP / LMT / bracket)
"""
from __future__ import annotations

from .client import (
    AccountSummary,
    IBKRAuthError,
    IBKRClient,
    IBKRCompetingSessionError,
    IBKRError,
    IBKROrderHalted,
)
from .gateway import GatewayHealth, check, keepalive_loop, wait_for_auth

__all__ = [
    "AccountSummary",
    "IBKRClient",
    "IBKRError",
    "IBKRAuthError",
    "IBKRCompetingSessionError",
    "IBKROrderHalted",
    "GatewayHealth",
    "check",
    "wait_for_auth",
    "keepalive_loop",
]
