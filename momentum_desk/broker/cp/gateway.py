"""Gateway auth polling + keepalive loop + health snapshot.

The IBKR CP Gateway is a local process (auto-started by ibeam in the container).
After it's up, login + the IBKR Mobile push 2FA happen in the browser/ibeam. Our
code polls until ``authenticated=true, connected=true, competing=false`` and then
sends a periodic ``/tickle`` to keep the session alive. The session dies ~24h
after login and after any competing login.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from .client import IBKRAuthError, IBKRClient, IBKRCompetingSessionError

log = logging.getLogger("momentum_desk.broker.cp")


@dataclass
class GatewayHealth:
    """Snapshot of gateway auth state, surfaced on the dashboard."""

    authenticated: bool
    connected: bool
    competing: bool
    message: str = ""
    last_tickle_at: datetime | None = None

    @property
    def ok(self) -> bool:
        return self.authenticated and self.connected and not self.competing

    def as_dict(self) -> dict:
        return {
            "authenticated": self.authenticated,
            "connected": self.connected,
            "competing": self.competing,
            "ok": self.ok,
            "message": self.message,
            "last_tickle_at": self.last_tickle_at.isoformat() if self.last_tickle_at else None,
        }


async def check(client: IBKRClient, *, last_tickle_at: datetime | None = None) -> GatewayHealth:
    """Return a GatewayHealth snapshot for the dashboard banner."""
    try:
        status = await client.auth_status()
    except Exception as e:  # noqa: BLE001 — health must never raise
        log.warning("gateway check failed: %s", e)
        return GatewayHealth(False, False, False, f"auth_status error: {e}", last_tickle_at)
    return GatewayHealth(
        authenticated=bool(status.get("authenticated", False)),
        connected=bool(status.get("connected", False)),
        competing=bool(status.get("competing", False)),
        message=str(status.get("message") or ""),
        last_tickle_at=last_tickle_at,
    )


async def wait_for_auth(client: IBKRClient, *, timeout_s: int = 300, poll_s: int = 3) -> None:
    """Poll /iserver/auth/status until authenticated. Raises IBKRAuthError on
    timeout, or IBKRCompetingSessionError if another session holds the auth."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        status = await client.auth_status()
        if status.get("competing"):
            raise IBKRCompetingSessionError(status.get("message") or "competing session")
        if status.get("authenticated") and status.get("connected"):
            log.info("gateway authenticated: %s", status.get("message"))
            return
        if asyncio.get_event_loop().time() >= deadline:
            raise IBKRAuthError(f"gateway did not authenticate within {timeout_s}s (last: {status})")
        log.info("gateway waiting for auth: %s", status)
        await asyncio.sleep(poll_s)


async def keepalive_loop(client: IBKRClient, *, interval_s: int = 60, state: dict | None = None) -> None:
    """Background tickle loop. Logs session_expired if auth drops. If ``state``
    is given, stamps ``state['last_tickle_at']`` after each successful tickle so
    the dashboard can show liveness. Cancel by cancelling the task."""
    log.info("keepalive loop starting (interval=%ss)", interval_s)
    try:
        while True:
            try:
                await client.tickle()
                if state is not None:
                    state["last_tickle_at"] = datetime.now(UTC)
            except IBKRAuthError as e:
                log.warning("session expired: %s", e)
            except IBKRCompetingSessionError as e:
                log.warning("competing session: %s", e)
            except Exception as e:  # noqa: BLE001
                log.warning("tickle failed: %s", e)
            await asyncio.sleep(interval_s)
    except asyncio.CancelledError:
        log.info("keepalive loop cancelled")
        return
