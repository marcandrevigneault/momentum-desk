"""FastAPI backend: runs the scan loop and streams ranked signals to the
dashboard over a WebSocket, plus a couple of REST endpoints for the initial
load and the header.

    uvicorn momentum_desk.server:app --reload --port 8000

Defaults to the mock feed, so it serves live-looking data with no key.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .backtest.data import MinuteBar
from .config import AppConfig, build_adapter, load_config
from .dryrun import supported
from .journal import Journal
from .live_engine import LiveEngine
from .live_feed import MinuteBarAggregator
from .models import Signal, Snapshot
from .paper import PaperDesk
from .risk import RiskEngine
from .scanner import ScannerEngine

_HISTORY_CAP = 240   # intraday points kept per symbol for the chart
_SESSION_OPEN_TOD = 240    # 04:00 ET — premarket; engine watches from here
_SESSION_CLOSE_TOD = 1200  # 20:00 ET — after-hours close; stop polling beyond


def _et_now():
    import datetime as _dt
    from zoneinfo import ZoneInfo
    return _dt.datetime.now(ZoneInfo("America/New_York"))


def _et_day() -> str:
    return _et_now().strftime("%Y-%m-%d")


def _in_session_window() -> bool:
    """Weekday and within premarket→after-hours — when it's worth polling the
    feed. Bounds cost: no point hammering the data API overnight/weekends."""
    now = _et_now()
    if now.weekday() >= 5:                       # Sat/Sun
        return False
    tod = now.hour * 60 + now.minute
    return _SESSION_OPEN_TOD <= tod <= _SESSION_CLOSE_TOD


def _market_phase() -> str:
    """Coarse US-equity session phase (no holiday calendar): regular | extended |
    closed. 'regular' is 09:30–16:00 ET on a weekday — the only window where a
    real-time feed should show sub-minute prints, so the only window where a
    stale feed actually means 'delayed'."""
    now = _et_now()
    if now.weekday() >= 5:
        return "closed"
    tod = now.hour * 60 + now.minute
    if 570 <= tod < 960:                          # 09:30–16:00
        return "regular"
    if _SESSION_OPEN_TOD <= tod <= _SESSION_CLOSE_TOD:
        return "extended"
    return "closed"


class ScannerService:
    """Holds the live pipeline and produces one serializable scan on demand."""

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.adapter = build_adapter(cfg)
        self.scanner = ScannerEngine(cfg.scanner)
        self.risk = RiskEngine(cfg.risk)
        self.desk = PaperDesk(self.risk)
        self.last_price: dict[str, float] = {}
        # live tape → closed MinuteBars (the shape the reconciled engine eats);
        # retained per symbol so /api/live/bars can show the live feed is sound.
        self.aggregator = MinuteBarAggregator()
        self.live_bars: dict[str, list[MinuteBar]] = {}
        # the reconciled engine run on the live tape — intended orders only,
        # NOTHING transmitted. None until a single-leg strategy is attached.
        self._strategy = None
        self.live: LiveEngine | None = None
        self.latest: dict | None = None     # last scan result, for headless WS reads
        # live order transmission (C4) — OFF unless explicitly armed in lifespan.
        self.armed = False                  # master switch: send real paper orders
        self.entries_halted = False         # daily-loss breaker (exits still allowed)
        self.pending_orders: list[dict] = []   # intended events awaiting transmission
        self.transmitted: list[dict] = []      # log of send/skip/halt outcomes
        self.stop_orders: dict[str, str] = {}  # symbol -> resting protective-stop order id
        self._journal: Journal | None = None   # per-session JSONL (lazy, day-rolled)
        self._journal_day = ""

    @property
    def journal(self) -> Journal:
        """The session journal (journal/live-YYYY-MM-DD.jsonl), rolled daily.
        Every engine intent, transmit decision, and order outcome lands here so
        the day can be reviewed with `python -m momentum_desk.journal`."""
        day = _et_day()
        if self._journal is None or self._journal_day != day:
            self._journal = Journal(Path("journal") / f"live-{day}.jsonl")
            self._journal_day = day
        return self._journal

    def set_strategy(self, strategy) -> None:
        """Attach (or detach) the strategy the live engine evaluates on the tape."""
        self._strategy = strategy
        self._rebuild_live(_et_day())

    def _rebuild_live(self, day: str) -> None:
        if self._strategy is None or not supported(self._strategy):
            self.live = None
            return
        self.live = LiveEngine(self._strategy, account_equity=self.cfg.risk.account_equity,
                               day=day)

    def _aggregate(self, snaps: list[Snapshot]) -> None:
        if self.live is not None and self.live.day != (today := _et_day()):
            self._rebuild_live(today)        # fresh session → fresh trackers
        for s in snaps:
            if self.live is not None:
                self.live.observe(s)         # register gate-passing candidates
            bar = self.aggregator.ingest(s)
            if bar is not None:
                buf = self.live_bars.setdefault(s.symbol, [])
                buf.append(bar)
                if len(buf) > _HISTORY_CAP:
                    del buf[: len(buf) - _HISTORY_CAP]
                if self.live is not None:
                    ev = self.live.on_bar(s.symbol, bar)   # intended entries/exits
                    if ev is not None and ev.get("kind") in ("entry", "exit", "reject"):
                        # journal every engine intent, armed or dry-run — reviewing
                        # decisions is the point, and dry-run days count too
                        self.journal.record(
                            "signal", intent=ev.get("kind"),
                            **{k: v for k, v in ev.items() if k != "kind"})
                    if self.armed and ev is not None and ev.get("kind") in ("entry", "exit"):
                        self.pending_orders.append(ev)     # drained by the engine loop

    def _record_prices(self, snaps: list[Snapshot]) -> None:
        for s in snaps:
            self.last_price[s.symbol] = s.last

    def stop_for(self, snap: Snapshot) -> float:
        return round(snap.last * 0.95, 2)   # illustrative 5% initial stop

    async def scan_once(self) -> dict:
        # adapters do blocking I/O (HTTP); keep the event loop free
        snaps = await asyncio.to_thread(lambda: list(self.adapter.poll()))
        by_symbol = {s.symbol: s for s in snaps}
        self._record_prices(snaps)
        self._aggregate(snaps)
        self.desk.update(self.last_price)   # trail stops + auto-exit on stop/target
        signals = self.scanner.scan(snaps)
        prices = self.last_price
        data_ts = [s.data_ts for s in snaps if s.data_ts > 0]
        feed_age = round(time.time() - max(data_ts), 1) if data_ts else None
        self.latest = {
            "ts": max((s.ts for s in snaps), default=0.0),
            "feed": self.adapter.name,
            "feed_age_s": feed_age,          # true data delay (None if feed has no timestamps)
            "market_phase": _market_phase(),
            "mode": self.cfg.mode,
            "count": len(signals),
            "signals": [self._signal_dict(s, by_symbol.get(s.symbol)) for s in signals],
            "account": self.desk.account_view(prices),
            "positions": self.desk.positions_view(prices),
        }
        return self.latest

    def _signal_dict(self, s: Signal, snap) -> dict:
        d = {
            "symbol": s.symbol, "score": s.score, "last": s.last,
            "gap_pct": s.gap_pct, "relative_volume": s.relative_volume,
            "extension_above_vwap_pct": s.extension_above_vwap_pct,
            "float_millions": s.float_millions, "has_news": s.has_news,
            "news_headline": s.news_headline, "actionable": s.actionable,
            "flags": [f.value for f in s.flags], "held": s.symbol in self.desk.open,
        }
        # the trade conditions the cockpit draws on the chart: entry / stop / target / trail
        if snap is not None:
            stop = self.stop_for(snap)
            plan = self.risk.plan(snap, entry=s.last, stop=stop)
            d["plan"] = {
                "ok": plan.ok, "shares": plan.shares, "entry": plan.entry, "stop": plan.stop,
                "target": round(plan.entry + self.desk.target_r * (plan.entry - plan.stop), 4),
                "trail_pct": self.desk.trail_pct,
                "risk_dollars": plan.risk_dollars, "reasons": plan.reasons,
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


async def _transmit_pending(svc: ScannerService) -> None:
    """Drain the intended-order queue to the IBKR paper account. The SINGLE place
    a real order is sent. Paper-only hard stop; entries face the daily-loss
    breaker + trading window and carry a broker-resident protective stop; exits
    cancel the resting stop and close while the broker still holds the symbol.
    Never raises."""
    if not svc.armed or not svc.pending_orders:
        svc.pending_orders.clear()
        return
    from .live_transmit import decide, order_ids, transmit_entry, transmit_exit
    client = getattr(app.state, "ibkr_client", None)
    if client is None:
        svc.pending_orders.clear()
        return
    try:
        account_id = client.account_id or await client.resolve_account_id()
    except Exception as e:  # noqa: BLE001 — gateway not authenticated yet
        print(f"[trade] no account (gateway not ready?): {e}; holding queue")
        return
    paper = account_id.upper().startswith("DU")
    try:
        held = {p.symbol.upper() for p in await client.get_positions(account_id)}
    except Exception:  # noqa: BLE001
        held = set()
    in_window = _in_session_window()

    queue, svc.pending_orders = svc.pending_orders, []
    for ev in queue:
        symbol = ev.get("symbol", "?")
        d = decide(ev, armed=svc.armed, entries_halted=svc.entries_halted,
                   paper=paper, in_window=in_window, held=held)
        rec = {**ev, "decision": d.action, "decision_reason": d.reason}
        action = {"send": "taken", "skip": "skipped"}.get(d.action, d.action)
        svc.journal.log_decision(symbol, action, d.reason, intent=ev.get("kind"))
        if d.action == "halt":
            svc.armed = False                # paper assertion failed — kill the switch
            print(f"[trade] HALT: {d.reason}. disarmed.")
            svc.transmitted.append(rec)
            break
        if d.action == "skip":
            svc.transmitted.append(rec)
            continue
        try:
            if d.side == "BUY":
                # entry parent + broker-resident protective stop, one submission —
                # a crashed desk never leaves a naked position
                reply = await transmit_entry(client, account_id, symbol,
                                             ev["shares"], ev["stop"])
                ids = order_ids(reply)
                stop_id = ids[1] if len(ids) > 1 else None
                if stop_id:
                    svc.stop_orders[symbol.upper()] = stop_id
                rec.update(order_ids=ids, stop_order_id=stop_id)
                held.add(symbol.upper())
            else:
                # cancel the resting stop first (best-effort), then close
                stop_id = svc.stop_orders.pop(symbol.upper(), None)
                reply = await transmit_exit(client, account_id, symbol,
                                            ev["shares"], stop_order_id=stop_id)
                rec["stop_order_id"] = stop_id
            rec["transmitted"] = True
            rec["broker_reply"] = reply
            svc.journal.log_fill({
                "symbol": symbol, "side": d.side, "shares": ev.get("shares"),
                "entry": ev.get("entry"), "stop": ev.get("stop"),
                "exit": ev.get("exit"), "pnl": ev.get("pnl"),
                "order_ids": rec.get("order_ids") or order_ids(reply),
                "stop_order_id": rec.get("stop_order_id"),
            })
            print(f"[trade] SENT {d.side} {ev['shares']} {symbol} -> {reply}")
        except Exception as e:  # noqa: BLE001
            rec["transmitted"] = False
            rec["error"] = str(e)
            svc.journal.record("error", symbol=symbol, intent=ev.get("kind"), error=str(e))
            print(f"[trade] send failed {symbol}: {e}")
        svc.transmitted.append(rec)

    # daily-loss breaker (proxy on the engine's intended day P&L): halt new entries
    if svc.live is not None and not svc.entries_halted:
        day_pnl = sum(o.get("pnl", 0.0) for o in svc.live.closed)
        limit = -abs(svc.cfg.risk.max_daily_loss_pct / 100.0 * svc.cfg.risk.account_equity)
        if day_pnl <= limit:
            svc.entries_halted = True
            print(f"[trade] daily-loss breaker tripped: {day_pnl:.0f} <= {limit:.0f}; "
                  "halting new entries (exits still allowed)")


async def _engine_loop(svc: ScannerService, interval_s: float) -> None:
    """Headless ticker: poll the feed and drive the aggregator + live engine on a
    schedule, independent of any dashboard WebSocket. Only polls inside the
    session window. Swallows its own errors so one bad tick never kills the loop.
    Dry-run computes intended orders only; when armed it also drains them
    through ``_transmit_pending``."""
    print(f"[engine] live-intent loop started (interval={interval_s}s, dry-run)")
    while True:
        try:
            if _in_session_window():
                await svc.scan_once()
                if svc.armed:
                    await _transmit_pending(svc)   # send any queued paper orders
                await asyncio.sleep(interval_s)
            else:
                await asyncio.sleep(60)      # idle off-hours, cheap
        except asyncio.CancelledError:
            raise
        except Exception as e:               # noqa: BLE001
            print(f"[engine] tick error: {e}")
            await asyncio.sleep(interval_s)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.service = ScannerService(load_config())
    cfg = app.state.service.cfg
    print(f"[server] feed={app.state.service.adapter.name} mode={cfg.mode} "
          f"interval={cfg.scan_interval_s}s")

    # Strategy Lab store (SQLite on the data volume), seeded with the canonical
    # strategies so the leaderboard isn't empty on a fresh deploy.
    from .edge.lab import seed as _seed_lab
    from .edge.store import LabStore
    app.state.lab = LabStore(os.environ.get("LAB_DB", "data/lab.db"))
    _seed_lab(app.state.lab)

    # Live engine — run the active Lab strategy on the live tape (dry-run; nothing
    # transmitted). Opt-in via LIVE_ENGINE_ENABLED so the data feed isn't polled
    # (and billed) unless wanted. The headless loop makes it autonomous — no
    # dashboard tab required.
    app.state.engine_task = None
    if os.environ.get("LIVE_ENGINE_ENABLED", "").strip().lower() in ("1", "true", "yes"):
        svc = app.state.service
        active = app.state.lab.get_active()
        strat = app.state.lab.get_strategy(active) if active else None
        if strat is not None and supported(strat):
            svc.set_strategy(strat)
            interval = float(os.environ.get("LIVE_ENGINE_INTERVAL_S", "15"))
            app.state.engine_task = asyncio.create_task(_engine_loop(svc, interval))
            # arming real paper orders requires ALL of: engine on, IBKR gateway on,
            # and the explicit LIVE_TRADING=armed flag. Paper-only is re-checked at
            # transmit time (DU account assertion), so this is defence-in-depth.
            ibkr_on = os.environ.get("IBKR_ENABLED", "").strip().lower() in ("1", "true", "yes")
            armed = os.environ.get("LIVE_TRADING", "").strip().lower() == "armed"
            svc.armed = bool(armed and ibkr_on)
            mode = "ARMED (real paper orders)" if svc.armed else "dry-run"
            if armed and not ibkr_on:
                print("[server] LIVE_TRADING=armed ignored: IBKR_ENABLED not set")
            print(f"[server] live engine attached: {active} ({mode}, interval={interval}s)")
        else:
            print(f"[server] live engine NOT started: active={active!r} not single-leg")

    # IBKR Client Portal keepalive — only when enabled (set IBKR_ENABLED=true in
    # the container, where the gateway + ibeam run). The tickle loop swallows its
    # own errors, so a not-yet-authenticated gateway just logs and retries.
    app.state.ibkr_client = None
    app.state.ibkr_state = {"last_tickle_at": None}
    app.state.ibkr_task = None
    if os.environ.get("IBKR_ENABLED", "").strip().lower() in ("1", "true", "yes"):
        from .broker import cp
        client = cp.IBKRClient(cfg.ibkr.gateway_url, account_id=cfg.ibkr.account_id)
        app.state.ibkr_client = client
        app.state.ibkr_task = asyncio.create_task(
            cp.keepalive_loop(client, interval_s=60, state=app.state.ibkr_state)
        )
        print(f"[server] IBKR CP keepalive started -> {cfg.ibkr.gateway_url}")

    yield

    engine_task = getattr(app.state, "engine_task", None)
    if engine_task is not None:
        engine_task.cancel()
    task = getattr(app.state, "ibkr_task", None)
    if task is not None:
        task.cancel()
    client = getattr(app.state, "ibkr_client", None)
    if client is not None:
        await client.aclose()
    lab = getattr(app.state, "lab", None)
    if lab is not None:
        lab.close()


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


@app.get("/api/ibkr/status")
async def ibkr_status() -> dict:
    """Live IBKR Client Portal gateway health for the dashboard banner. When the
    gateway is authenticated (after the one-time phone 2FA), ok=true. Returns
    enabled=false locally where the gateway isn't running."""
    client = getattr(app.state, "ibkr_client", None)
    if client is None:
        return {"enabled": False, "ok": False,
                "message": "IBKR not enabled (set IBKR_ENABLED=true to run the gateway)"}
    from .broker import cp
    state = getattr(app.state, "ibkr_state", {})
    health = await cp.check(client, last_tickle_at=state.get("last_tickle_at"))
    paper = app.state.service.cfg.ibkr.paper
    return {"enabled": True, "account_id": client.account_id, "paper": paper, **health.as_dict()}


@app.get("/api/ibkr/portfolio")
async def ibkr_portfolio() -> dict:
    """Real IBKR paper-account NAV + open positions, READ-ONLY. Lets the Cockpit
    show the actual DU… account next to the sim desk before any order is ever
    placed. Fetches nothing-destructive; never transmits an order."""
    client = getattr(app.state, "ibkr_client", None)
    if client is None:
        return {"enabled": False, "reason": "IBKR not enabled (set IBKR_ENABLED=true)"}
    try:
        account_id = client.account_id or await client.resolve_account_id()
        summary = await client.get_summary(account_id)
        positions = await client.get_positions(account_id)
    except Exception as e:  # noqa: BLE001 — gateway may be unauthenticated/down
        return {"enabled": True, "ok": False, "reason": str(e)}
    paper = account_id.upper().startswith("DU")
    return {
        "enabled": True, "ok": True, "account_id": account_id, "paper": paper,
        "nav": float(summary.nav), "cash": float(summary.cash),
        "unrealized_pnl": float(summary.unrealized_pnl),
        "realized_pnl": None if summary.realized_pnl is None else float(summary.realized_pnl),
        "positions": [{"symbol": p.symbol, "quantity": p.quantity, "avg_price": p.avg_price}
                      for p in positions],
    }


# ---- Strategy Lab: one API over strategies, runs, the ranked leaderboard, and
# the active pick (consolidates what the analyser/sim/combo/optimize pages did).

@app.get("/api/lab/strategies")
async def lab_strategies() -> dict:
    return {"strategies": [s.to_dict() for s in app.state.lab.list_strategies()],
            "active": app.state.lab.get_active()}


@app.post("/api/lab/strategies")
async def lab_save_strategy(payload: dict) -> dict:
    from .edge.strategy import Strategy
    strat = Strategy.from_dict(payload)
    if not strat.name:
        return {"ok": False, "error": "strategy needs a name"}
    app.state.lab.save_strategy(strat)
    return {"ok": True, "strategy": strat.to_dict()}


@app.delete("/api/lab/strategies/{name}")
async def lab_delete_strategy(name: str) -> dict:
    app.state.lab.delete_strategy(name)
    return {"ok": True}


@app.post("/api/lab/strategies/{name}/rename")
async def lab_rename_strategy(name: str, payload: dict) -> dict:
    new = (payload.get("new_name") or "").strip()
    if not new:
        return {"ok": False, "error": "new_name required"}
    ok = app.state.lab.rename_strategy(name, new)
    return {"ok": ok, "error": None if ok else "name missing or already taken", "name": new}


@app.get("/api/lab/leaderboard")
async def lab_leaderboard(rank_by: str = "expectancy_r", window: str | None = None, limit: int = 100) -> dict:
    return {"rank_by": rank_by, "window": window,
            "runs": app.state.lab.leaderboard(rank_by=rank_by, window=window, limit=limit)}


@app.get("/api/lab/runs/{run_id}")
async def lab_run(run_id: int) -> dict:
    run = app.state.lab.get_run(run_id)
    return run or {"error": "no such run"}


@app.post("/api/lab/run")
async def lab_run_strategy(payload: dict) -> dict:
    """Run a strategy (by name from the store, or an inline config) on synthetic
    data over the window, persist it, and return the result. Heavy work runs off
    the event loop."""
    from .edge.lab import best_data_source, run_only
    from .edge.strategy import Strategy
    name = payload.get("name")
    window = payload.get("window", "1y")
    strat = app.state.lab.get_strategy(name) if name else None
    if strat is None and isinstance(payload.get("strategy"), dict):
        strat = Strategy.from_dict(payload["strategy"])
    if strat is None:
        return {"ok": False, "error": "provide a known strategy name or an inline strategy config"}
    # compute off the event loop; write to the DB on this (the connection's) thread
    ds = best_data_source()
    result = await asyncio.to_thread(run_only, strat, window=window, data_source=ds)
    run_id = app.state.lab.save_run(strat, window, ds, result)
    return {"ok": True, "run_id": run_id, "window": window, "data_source": ds, **asdict_result(result)}


@app.get("/api/lab/gauntlet")
async def lab_gauntlet(strategy: str) -> dict:
    """The cached evaluation gauntlet (bootstrap CI, deflated Sharpe, walk-forward)
    for a strategy's entry — 'does this survive?'. None for multi-leg combos."""
    from .edge.lab import gauntlet_key
    strat = app.state.lab.get_strategy(strategy)
    if strat is None:
        return {"available": False, "reason": "unknown strategy"}
    key = gauntlet_key(strat)
    if key is None:
        return {"available": False, "reason": "gauntlet evaluates a single entry — not multi-leg combos"}
    g = app.state.lab.get_gauntlet(key)
    return {"available": bool(g), "gauntlet": g} if g else {"available": False, "reason": "not computed yet"}


@app.get("/api/lab/dryrun")
async def lab_dryrun(strategy: str) -> dict:
    """Live dry-run preview for a strategy: what the reconciled engine would have
    traded on the most recent day (sourced from its run — proven identical to the
    live engine; nothing transmitted). Single-leg only."""
    from .dryrun import supported
    strat = app.state.lab.get_strategy(strategy)
    if strat is None:
        return {"available": False, "reason": "unknown strategy"}
    if not supported(strat):
        return {"available": False, "reason": "dry-run is single-leg only (this strategy is multi-leg)"}
    row = next((r for r in app.state.lab.leaderboard(window="1y") if r["strategy"] == strategy), None)
    if row is None:
        return {"available": False, "reason": "no run yet"}
    trades = app.state.lab.get_run(row["id"])["result"].get("trades", [])
    if not trades:
        return {"available": True, "day": None, "orders": [], "day_pnl": 0.0}
    last_day = max(t["day"] for t in trades)
    orders = [t for t in trades if t["day"] == last_day]
    return {"available": True, "strategy": strategy, "day": last_day, "orders": orders,
            "day_pnl": round(sum(t["pnl"] for t in orders), 2)}


@app.get("/api/lab/active")
async def lab_get_active() -> dict:
    return {"active": app.state.lab.get_active()}


@app.post("/api/lab/active")
async def lab_set_active(payload: dict) -> dict:
    name = payload.get("name")
    if not name:
        return {"ok": False, "error": "name required"}
    app.state.lab.set_active(name)
    return {"ok": True, "active": name}


def asdict_result(result) -> dict:
    from dataclasses import asdict as _asdict
    return {"result": _asdict(result)}


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


def _polygon_key() -> str:
    return os.environ.get("POLYGON_API_KEY", "") or load_config().polygon_api_key


@app.get("/api/bars/{symbol}")
async def bars(symbol: str, tf: str = "1m") -> dict:
    """Real OHLC candles from Polygon for the chart — proper history on click
    instead of waiting for the slow live stream to accumulate points."""
    import datetime as dt
    import urllib.parse
    import urllib.request

    key = _polygon_key()
    if not key:
        return {"symbol": symbol, "tf": tf, "candles": [], "error": "no Polygon key configured"}
    mult, span, days = {"1m": (1, "minute", 4), "5m": (5, "minute", 10),
                        "1d": (1, "day", 200)}.get(tf, (1, "minute", 4))
    today = dt.date.today()
    frm = (today - dt.timedelta(days=days)).isoformat()
    q = urllib.parse.urlencode({"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": key})
    url = f"https://api.polygon.io/v2/aggs/ticker/{symbol.upper()}/range/{mult}/{span}/{frm}/{today.isoformat()}?{q}"

    def fetch():
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read().decode())

    try:
        data = await asyncio.to_thread(fetch)
    except Exception as e:  # noqa: BLE001
        return {"symbol": symbol, "tf": tf, "candles": [], "error": str(e)}
    candles = [{"time": int(b["t"] / 1000), "open": b["o"], "high": b["h"],
                "low": b["l"], "close": b["c"], "volume": int(b.get("v", 0))}
               for b in (data.get("results") or [])]
    return {"symbol": symbol, "tf": tf, "candles": candles}


@app.get("/api/positions")
async def positions() -> dict:
    svc: ScannerService = app.state.service
    return {"positions": svc.desk.positions_view(svc.last_price),
            "account": svc.desk.account_view(svc.last_price)}


@app.get("/api/trades")
async def trades() -> dict:
    return {"trades": app.state.service.desk.trades_view()}


@app.get("/api/live/bars/{symbol}")
async def live_bars(symbol: str, limit: int = 60) -> dict:
    """The closed MinuteBars the live aggregator has built from the tape for one
    symbol — the exact shape the reconciled engine consumes. Observability for
    the live feed; transmits nothing, places nothing."""
    from dataclasses import asdict
    svc: ScannerService = app.state.service
    bars = svc.live_bars.get(symbol.upper(), [])[-limit:]
    return {"symbol": symbol.upper(), "feed": svc.adapter.name,
            "count": len(bars), "bars": [asdict(b) for b in bars]}


@app.get("/api/live/bars")
async def live_bars_index() -> dict:
    """Which symbols the live aggregator currently has closed bars for."""
    svc: ScannerService = app.state.service
    return {"feed": svc.adapter.name,
            "symbols": {sym: len(bars) for sym, bars in sorted(svc.live_bars.items())}}


@app.get("/api/live/intent")
async def live_intent() -> dict:
    """What the reconciled engine WOULD trade on the live tape for the active
    strategy — watched candidates, intended entries/exits, day P&L. Proven
    identical to the dry-run/backtest. NOTHING is transmitted; no order is placed.
    Enable with LIVE_ENGINE_ENABLED + a single-leg active strategy."""
    svc: ScannerService = app.state.service
    if svc.live is None:
        active = app.state.lab.get_active()
        return {"available": False, "armed": False,
                "reason": f"live engine not attached (active={active!r}; "
                          "set LIVE_ENGINE_ENABLED and pick a single-leg strategy)"}
    snap = svc.live.snapshot()
    snap.update(available=True, armed=svc.armed, entries_halted=svc.entries_halted,
                feed=svc.adapter.name, in_session=_in_session_window(),
                transmitted=svc.transmitted[-50:], transmitted_count=len(svc.transmitted))
    return snap


@app.get("/api/edge")
async def edge_screen() -> dict:
    """Phase-1 edge screen: per-feature information coefficient + decile-lift for
    each session. Prefers fresh results on the volume (data/edge_screen_*.json,
    written by scripts/screen_edge.py); falls back to the committed snapshot so
    the hosted app always shows the latest real-data findings."""
    snap_path = Path(__file__).parent / "edge" / "snapshot.json"
    snapshot: dict = {}
    if snap_path.exists():
        try:
            snapshot = json.loads(snap_path.read_text())
        except Exception:  # noqa: BLE001
            snapshot = {}
    out = {"generated": snapshot.get("generated"), "days": snapshot.get("days"),
           "data": snapshot.get("data"), "sessions": {}, "source": "snapshot"}
    for session in ("premarket", "intraday"):
        fresh = Path(f"data/edge_screen_{session}.json")
        if fresh.exists():
            try:
                out["sessions"][session] = json.loads(fresh.read_text())
                out["source"] = "live"
                continue
            except Exception:  # noqa: BLE001
                pass
        if session in snapshot:
            out["sessions"][session] = snapshot[session]
    return out


_EVAL_CACHE: dict = {}


def _load_eval_cache() -> dict:
    if not _EVAL_CACHE:
        for p in (Path("data/eval_cache.json"), Path(__file__).parent / "edge" / "eval_cache.json"):
            if p.exists():
                try:
                    _EVAL_CACHE.update(json.loads(p.read_text()))
                    break
                except Exception:  # noqa: BLE001
                    pass
    return _EVAL_CACHE


@app.get("/api/tuner")
async def tuner_meta() -> dict:
    """What the live variable editor needs to render: sessions + exit policies."""
    c = _load_eval_cache()
    return {"sessions": list(c.get("sessions", {}).keys()), "policies": c.get("policies", []),
            "days": c.get("days"), "available": bool(c.get("sessions"))}


@app.get("/api/evaluate")
async def evaluate_config(session: str = "intraday", max_ext: float | None = None,
                          rvol_min: float = 0.0, rvol_max: float | None = None,
                          min_move: float = 0.0, exit: str = "pct_trail_10") -> dict:
    """Score one variable combination off the precomputed cache — instant. Drives
    the live variable editor (#6)."""
    from .edge.optimize import evaluate_cache as evaluate
    c = _load_eval_cache()
    events = c.get("sessions", {}).get(session, [])
    if not events:
        return {"n": 0, "error": "no cache for session"}
    return evaluate(events, max_ext=max_ext, rvol_min=rvol_min, rvol_max=rvol_max,
                    min_move=min_move, exit_policy=exit)


@app.get("/api/rules")
async def rules_results() -> dict:
    """AND/OR entry+exit rule combos (#4): compare composed rules head-to-head."""
    fresh = Path("data/rules.json")
    if fresh.exists():
        try:
            return {"source": "live", **json.loads(fresh.read_text())}
        except Exception:  # noqa: BLE001
            pass
    snap = Path(__file__).parent / "edge" / "rules_snapshot.json"
    if snap.exists():
        try:
            return {"source": "snapshot", **json.loads(snap.read_text())}
        except Exception:  # noqa: BLE001
            pass
    return {"source": "none", "results": []}


@app.get("/api/gauntlet")
async def gauntlet() -> dict:
    """Phase-3 evaluation gauntlet: the candidate strategy's verdict per session
    (bootstrap CI, deflated Sharpe, walk-forward, regime, holdout). Prefers fresh
    data/gauntlet_*.json on the volume, else the committed snapshot."""
    snap_path = Path(__file__).parent / "edge" / "gauntlet_snapshot.json"
    snapshot: dict = {}
    if snap_path.exists():
        try:
            snapshot = json.loads(snap_path.read_text())
        except Exception:  # noqa: BLE001
            snapshot = {}
    out = {"generated": snapshot.get("generated"), "days": snapshot.get("days"),
           "data": snapshot.get("data"), "sessions": {}, "source": "snapshot"}
    for session in ("premarket", "intraday"):
        fresh = Path(f"data/gauntlet_{session}.json")
        if fresh.exists():
            try:
                out["sessions"][session] = json.loads(fresh.read_text())
                out["source"] = "live"
                continue
            except Exception:  # noqa: BLE001
                pass
        if session in snapshot:
            out["sessions"][session] = snapshot[session]
    return out


@app.get("/api/exitlab")
async def exit_lab() -> dict:
    """Phase-2 exit-policy lab: same entries, different exits, compared per
    session. Prefers fresh data/exit_lab_*.json on the volume, falls back to the
    committed snapshot so the hosted app always shows the latest findings."""
    snap_path = Path(__file__).parent / "edge" / "exit_snapshot.json"
    snapshot: dict = {}
    if snap_path.exists():
        try:
            snapshot = json.loads(snap_path.read_text())
        except Exception:  # noqa: BLE001
            snapshot = {}
    out = {"generated": snapshot.get("generated"), "days": snapshot.get("days"),
           "data": snapshot.get("data"), "slippage": snapshot.get("slippage"),
           "sessions": {}, "source": "snapshot"}
    for session in ("premarket", "intraday"):
        fresh = Path(f"data/exit_lab_{session}.json")
        if fresh.exists():
            try:
                out["sessions"][session] = json.loads(fresh.read_text())
                out["source"] = "live"
                continue
            except Exception:  # noqa: BLE001
                pass
        if session in snapshot:
            out["sessions"][session] = snapshot[session]
    return out


@app.post("/api/trade/open/{symbol}")
async def trade_open(symbol: str) -> dict:
    svc: ScannerService = app.state.service
    snaps = await asyncio.to_thread(lambda: list(svc.adapter.poll()))
    snap = next((s for s in snaps if s.symbol == symbol), None)
    if snap is None:
        return {"ok": False, "reasons": [f"{symbol} not in the current scan"]}
    return svc.desk.open_position(snap, entry=snap.last, stop=svc.stop_for(snap))


@app.post("/api/trade/close/{symbol}")
async def trade_close(symbol: str) -> dict:
    svc: ScannerService = app.state.service
    price = svc.last_price.get(symbol)
    if price is None:
        return {"ok": False, "reasons": [f"no price for {symbol}"]}
    trade = svc.desk.close_position(symbol, price, "manual")
    if trade is None:
        return {"ok": False, "reasons": [f"no open position in {symbol}"]}
    return {"ok": True, "pnl": trade.pnl, "exit": trade.exit}


@app.websocket("/ws/signals")
async def ws_signals(ws: WebSocket) -> None:
    await ws.accept()
    svc: ScannerService = app.state.service
    headless = getattr(app.state, "engine_task", None) is not None
    try:
        while True:
            # when the headless engine loop owns polling, stream its cached tick
            # rather than double-polling (and double-billing) the feed.
            data = svc.latest if (headless and svc.latest is not None) else await svc.scan_once()
            await ws.send_json(data)
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
