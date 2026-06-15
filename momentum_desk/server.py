"""FastAPI backend: runs the scan loop and streams ranked signals to the
dashboard over a WebSocket, plus a couple of REST endpoints for the initial
load and the header.

    uvicorn momentum_desk.server:app --reload --port 8000

Defaults to the mock feed, so it serves live-looking data with no key.
"""
from __future__ import annotations

import asyncio
import base64
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import AppConfig, build_adapter, load_config
from .models import Signal
from .risk import RiskEngine
from .scanner import ScannerEngine


class ScannerService:
    """Holds the live pipeline and produces one serializable scan on demand."""

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.adapter = build_adapter(cfg)
        self.scanner = ScannerEngine(cfg.scanner)
        self.risk = RiskEngine(cfg.risk)

    async def scan_once(self) -> dict:
        # adapters do blocking I/O (HTTP); keep the event loop free
        snaps = await asyncio.to_thread(lambda: list(self.adapter.poll()))
        by_symbol = {s.symbol: s for s in snaps}
        signals = self.scanner.scan(snaps)
        return {
            "ts": max((s.ts for s in snaps), default=0.0),
            "feed": self.adapter.name,
            "mode": self.cfg.mode,
            "count": len(signals),
            "signals": [self._signal_dict(s, by_symbol.get(s.symbol)) for s in signals],
            "account": {
                "equity": self.risk.config.account_equity,
                "realized_pnl_today": round(self.risk.realized_pnl_today, 2),
                "daily_loss_limit_hit": self.risk.daily_loss_limit_hit,
            },
        }

    def _signal_dict(self, s: Signal, snap) -> dict:
        d = {
            "symbol": s.symbol, "score": s.score, "last": s.last,
            "gap_pct": s.gap_pct, "relative_volume": s.relative_volume,
            "extension_above_vwap_pct": s.extension_above_vwap_pct,
            "float_millions": s.float_millions, "has_news": s.has_news,
            "news_headline": s.news_headline, "actionable": s.actionable,
            "flags": [f.value for f in s.flags],
        }
        # attach an illustrative risk-sized plan (5% stop) for actionable names
        if s.actionable and snap is not None:
            plan = self.risk.plan(snap, entry=s.last, stop=round(s.last * 0.95, 2))
            d["plan"] = {
                "ok": plan.ok, "shares": plan.shares, "entry": plan.entry,
                "stop": plan.stop, "risk_dollars": plan.risk_dollars, "reasons": plan.reasons,
            }
        return d


class BasicAuthMiddleware:
    """Gate the whole app (HTTP + WebSocket) behind HTTP Basic Auth. Enabled
    only when DASHBOARD_PASSWORD is set, so local dev stays open. /api/health is
    exempt so platform health checks can reach it."""

    def __init__(self, app, username: str, password: str) -> None:
        self.app = app
        self._username = username
        self._password = password

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or scope.get("path") == "/api/health":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        if self._authorized(headers.get(b"authorization")):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"www-authenticate", b'Basic realm="Momentum Desk"'),
                    (b"content-type", b"text/plain; charset=utf-8"),
                ],
            })
            await send({"type": "http.response.body", "body": b"401 Unauthorized"})

    def _authorized(self, header: bytes | None) -> bool:
        if not header:
            return False
        try:
            scheme, _, param = header.decode().partition(" ")
            if scheme.lower() != "basic":
                return False
            user, _, pw = base64.b64decode(param).decode().partition(":")
        except Exception:
            return False
        # constant-time compares so we don't leak length/prefix via timing
        return (secrets.compare_digest(user, self._username)
                and secrets.compare_digest(pw, self._password))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.service = ScannerService(load_config())
    cfg = app.state.service.cfg
    print(f"[server] feed={app.state.service.adapter.name} mode={cfg.mode} "
          f"interval={cfg.scan_interval_s}s")
    yield


app = FastAPI(title="Momentum Desk", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# Password protection for public deploys; a no-op locally unless you set the env.
_DASH_PW = os.environ.get("DASHBOARD_PASSWORD", "")
if _DASH_PW:
    app.add_middleware(
        BasicAuthMiddleware,
        username=os.environ.get("DASHBOARD_USER", "admin"),
        password=_DASH_PW,
    )


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/api/config")
async def get_config() -> dict:
    svc: ScannerService = app.state.service
    sc, rk = svc.cfg.scanner, svc.cfg.risk
    return {
        "mode": svc.cfg.mode, "feed": svc.adapter.name,
        "scan_interval_s": svc.cfg.scan_interval_s,
        "scanner": {
            "min_price": sc.min_price, "max_price": sc.max_price,
            "max_float_millions": sc.max_float_millions,
            "min_relative_volume": sc.min_relative_volume,
            "min_gap_pct": sc.min_gap_pct, "require_news": sc.require_news,
            "max_extension_above_vwap_pct": sc.max_extension_above_vwap_pct,
        },
        "risk": {
            "account_equity": rk.account_equity,
            "max_risk_per_trade_pct": rk.max_risk_per_trade_pct,
            "max_daily_loss_pct": rk.max_daily_loss_pct,
            "max_pct_of_recent_volume": rk.max_pct_of_recent_volume,
        },
    }


@app.get("/api/signals")
async def signals() -> dict:
    return await app.state.service.scan_once()


@app.websocket("/ws/signals")
async def ws_signals(ws: WebSocket) -> None:
    await ws.accept()
    svc: ScannerService = app.state.service
    try:
        while True:
            await ws.send_json(await svc.scan_once())
            await asyncio.sleep(svc.cfg.scan_interval_s)
    except WebSocketDisconnect:
        pass


# Serve the built dashboard if present, so the Docker image is one deployable
# unit. Mounted last, so /api/* and /ws/* (registered above) take precedence.
# In local dev the dist may not exist — then this is simply skipped and you run
# the Vite dev server separately.
_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="web")
