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
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import AppConfig, build_adapter, load_config
from .models import Signal, Snapshot
from .paper import PaperDesk
from .risk import RiskEngine
from .scanner import ScanConfig, ScannerEngine

_HISTORY_CAP = 240   # intraday points kept per symbol for the chart


class ScannerService:
    """Holds the live pipeline and produces one serializable scan on demand."""

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.adapter = build_adapter(cfg)
        self.scanner = ScannerEngine(cfg.scanner)
        self.risk = RiskEngine(cfg.risk)
        self.desk = PaperDesk(self.risk)
        self.history: dict[str, list[dict]] = {}
        self.last_price: dict[str, float] = {}

    def _record_history(self, snaps: list[Snapshot]) -> None:
        for s in snaps:
            self.last_price[s.symbol] = s.last
            buf = self.history.setdefault(s.symbol, [])
            buf.append({"t": round(s.ts, 1), "last": s.last, "vwap": round(s.vwap, 4)})
            if len(buf) > _HISTORY_CAP:
                del buf[: len(buf) - _HISTORY_CAP]

    def stop_for(self, snap: Snapshot) -> float:
        return round(snap.last * 0.95, 2)   # illustrative 5% initial stop

    async def scan_once(self) -> dict:
        # adapters do blocking I/O (HTTP); keep the event loop free
        snaps = await asyncio.to_thread(lambda: list(self.adapter.poll()))
        by_symbol = {s.symbol: s for s in snaps}
        self._record_history(snaps)
        self.desk.update(self.last_price)   # trail stops + auto-exit on stop/target
        signals = self.scanner.scan(snaps)
        prices = self.last_price
        return {
            "ts": max((s.ts for s in snaps), default=0.0),
            "feed": self.adapter.name,
            "mode": self.cfg.mode,
            "count": len(signals),
            "signals": [self._signal_dict(s, by_symbol.get(s.symbol)) for s in signals],
            "account": self.desk.account_view(prices),
            "positions": self.desk.positions_view(prices),
        }

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.service = ScannerService(load_config())
    app.state.jobs = {}          # job_id -> {status, params, started, result, error}
    cfg = app.state.service.cfg
    print(f"[server] feed={app.state.service.adapter.name} mode={cfg.mode} "
          f"interval={cfg.scan_interval_s}s")

    # Strategy Lab store (SQLite on the data volume), seeded with the canonical
    # strategies so the leaderboard isn't empty on a fresh deploy.
    from .edge.lab import seed as _seed_lab
    from .edge.store import LabStore
    app.state.lab = LabStore(os.environ.get("LAB_DB", "data/lab.db"))
    _seed_lab(app.state.lab)

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


@app.get("/api/history/{symbol}")
async def history(symbol: str) -> dict:
    svc: ScannerService = app.state.service
    return {"symbol": symbol, "points": svc.history.get(symbol, [])}


@app.get("/api/bars/{symbol}")
async def bars(symbol: str, tf: str = "1m") -> dict:
    """Real OHLC candles from Massive for the chart — proper history on click
    instead of waiting for the slow live stream to accumulate points."""
    import datetime as dt
    import urllib.parse
    import urllib.request

    key = _massive_key()
    if not key:
        return {"symbol": symbol, "tf": tf, "candles": [], "error": "no Massive key configured"}
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


@app.post("/api/backtest")
async def backtest(session: str = "premarket", days: int = 60, target_r: float = 2.0,
                   slippage_pct: float = 0.1, max_hold: int = 60, time_exit_tod: int = 0) -> dict:
    """Run a backtest and return its equity curve, metrics, and trades for the
    visualizer. Uses synthetic data here (the hosted app has no data key), so
    results are an engine illustration, not strategy evidence."""
    from dataclasses import asdict

    from .backtest import Backtester, SyntheticHistory
    from .backtest.engine import BacktestConfig
    from .backtest.review import breakdowns

    session = session if session in ("premarket", "intraday", "regular") else "premarket"
    days = max(5, min(int(days), 120))

    def run():
        prov = SyntheticHistory(days=days, session=session)
        bt = BacktestConfig(session=session, target_r=target_r, max_hold_minutes=max_hold,
                            slippage_pct=slippage_pct, premarket_slippage_pct=slippage_pct,
                            time_exit_tod=int(time_exit_tod))
        return Backtester(prov, bt=bt).run()

    res = await asyncio.to_thread(run)
    bd = breakdowns(res.trades)
    out = {
        "synthetic": True,
        "session": session,
        "days": res.days,
        "metrics": asdict(res.metrics),
        "equity_curve": res.equity_curve,
        "trades": [asdict(t) for t in res.trades],
        "monthly": bd["monthly"],
        "yearly": bd["yearly"],
    }
    _save_run("synthetic", {"session": session, "days": days, "target_r": target_r,
                            "time_exit_tod": time_exit_tod}, out)
    return out


@app.get("/api/realrun")
async def realrun() -> dict:
    """Serve the latest local multi-year real-data run (scripts/realrun.py
    writes data/realrun.json). Absent on the hosted app — real runs are local."""
    p = Path("data/realrun.json")
    if not p.exists():
        return {"available": False}
    try:
        return {"available": True, **json.loads(p.read_text())}
    except Exception:  # noqa: BLE001
        return {"available": False}


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


@app.get("/api/simrun")
async def sim_run(window: str = "1y", compound: bool = False) -> dict:
    """Full end-to-end account simulation. `window` selects the horizon: "1y"
    (last year) or "5y" (last five years). `compound` sizes risk off the live
    book instead of the fixed start balance. Prefers a fresh run on the volume,
    else the committed snapshot."""
    suffix = ("_5y" if window == "5y" else "") + ("_c" if compound else "")
    fresh = Path(f"data/sim{'_5y' if window == '5y' else '_year'}{'_c' if compound else ''}.json")
    if fresh.exists():
        try:
            return {"source": "live", **json.loads(fresh.read_text())}
        except Exception:  # noqa: BLE001
            pass
    snap = Path(__file__).parent / "edge" / f"sim_snapshot{suffix}.json"
    if snap.exists():
        try:
            return {"source": "snapshot", **json.loads(snap.read_text())}
        except Exception:  # noqa: BLE001
            pass
    return {"source": "none", "trades": [], "metrics": {}}


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
    from .edge.tuner import evaluate
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


@app.get("/api/combos-optimize")
async def combos_optimize() -> dict:
    """Combo parameter sweep (#6): best config per combo + whether any combo beats
    intraday-alone. Drives the combo 'optimized' badge."""
    snap = Path(__file__).parent / "edge" / "combos_optimize_snapshot.json"
    if snap.exists():
        try:
            return {"source": "snapshot", **json.loads(snap.read_text())}
        except Exception:  # noqa: BLE001
            pass
    return {"source": "none", "results": []}


@app.get("/api/optimize")
async def optimize_results() -> dict:
    """Per-session optimizer results (#6): best config + deflated Sharpe + whether
    it's robust (DSR≥95%). Drives the 'optimized' badge."""
    snap = Path(__file__).parent / "edge" / "optimize_snapshot.json"
    if snap.exists():
        try:
            return {"source": "snapshot", **json.loads(snap.read_text())}
        except Exception:  # noqa: BLE001
            pass
    return {"source": "none", "sessions": {}}


_ACTIVE_STRATEGY = Path("data/active_strategy.json")


@app.get("/api/active-strategy")
async def get_active_strategy() -> dict:
    if _ACTIVE_STRATEGY.exists():
        try:
            return json.loads(_ACTIVE_STRATEGY.read_text())
        except Exception:  # noqa: BLE001
            pass
    return {"active": None}


@app.post("/api/active-strategy")
async def set_active_strategy(payload: dict) -> dict:
    """Persist the user's chosen 'active strategy' (the analyser's selection)."""
    _ACTIVE_STRATEGY.parent.mkdir(parents=True, exist_ok=True)
    rec = {"active": payload.get("active"), "label": payload.get("label", ""), "ts": time.time()}
    _ACTIVE_STRATEGY.write_text(json.dumps(rec))
    return {"ok": True, **rec}


@app.get("/api/combos")
async def combos_all(window: str = "1y") -> dict:
    """Named combos for the selector (each carries a full trade log). `window` =
    1y | 5y. Prefers a fresh file on the volume, else the committed snapshot."""
    suffix = "_5y" if window == "5y" else ""
    fresh = Path(f"data/combos{suffix}.json")
    if fresh.exists():
        try:
            return {"source": "live", **json.loads(fresh.read_text())}
        except Exception:  # noqa: BLE001
            pass
    snap = Path(__file__).parent / "edge" / f"combos_snapshot{suffix}.json"
    if snap.exists():
        try:
            return {"source": "snapshot", **json.loads(snap.read_text())}
        except Exception:  # noqa: BLE001
            pass
    return {"source": "none", "combos": {}}


@app.get("/api/combo")
async def combo_run(window: str = "1y") -> dict:
    """Multi-style combo: several strategy legs in one shared-capital book.
    `window` = "1y" | "5y". Prefers a fresh run on the volume, else the snapshot."""
    fresh = Path(f"data/combo{'_5y' if window == '5y' else '_real'}.json")
    if fresh.exists():
        try:
            return {"source": "live", **json.loads(fresh.read_text())}
        except Exception:  # noqa: BLE001
            pass
    snap = Path(__file__).parent / "edge" / f"combo_snapshot{'_5y' if window == '5y' else ''}.json"
    if snap.exists():
        try:
            return {"source": "snapshot", **json.loads(snap.read_text())}
        except Exception:  # noqa: BLE001
            pass
    return {"source": "none", "trades": [], "metrics": {}, "legs": []}


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


# ---- saved-runs store (persisted on the volume at /app/data) ----
_RUNS_DIR = Path("data/runs")


def _save_run(kind: str, params: dict, result: dict) -> str:
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    rid = f"{int(time.time())}-{uuid.uuid4().hex[:6]}"
    rec = {"id": rid, "ts": time.time(), "kind": kind, "params": params, "result": result}
    (_RUNS_DIR / f"{rid}.json").write_text(json.dumps(rec))
    # keep the store bounded: newest 60
    for old in sorted(_RUNS_DIR.glob("*.json"))[:-60]:
        try:
            old.unlink()
        except OSError:
            pass
    return rid


def _run_summary(rec: dict) -> dict:
    r, m, p = rec.get("result", {}), rec.get("result", {}).get("metrics", {}), rec.get("params", {})
    return {
        "id": rec["id"], "ts": rec.get("ts", 0), "kind": rec.get("kind", "?"),
        "synthetic": r.get("synthetic"), "session": r.get("session"), "days": r.get("days"),
        "trades": m.get("trades"), "expectancy_r": m.get("expectancy_r"),
        "total_pnl": m.get("total_pnl"), "max_drawdown_pct": m.get("max_drawdown_pct"),
        "target_r": p.get("target_r"), "time_exit_tod": p.get("time_exit_tod"),
    }


@app.get("/api/backtest/runs")
async def list_runs() -> dict:
    runs = []
    if _RUNS_DIR.is_dir():
        for f in sorted(_RUNS_DIR.glob("*.json"), reverse=True)[:60]:
            try:
                runs.append(_run_summary(json.loads(f.read_text())))
            except Exception:  # noqa: BLE001
                pass
    # surface a local multi-year realrun.json as a pinned entry, if present
    rr = Path("data/realrun.json")
    if rr.exists():
        try:
            d = json.loads(rr.read_text())
            m, bp = d.get("metrics", {}), d.get("best_params", {})
            runs.append({"id": "realrun", "ts": 0, "kind": "real-sweep", "synthetic": False,
                         "session": d.get("session"), "days": d.get("days"),
                         "trades": m.get("trades"), "expectancy_r": m.get("expectancy_r"),
                         "total_pnl": m.get("total_pnl"), "max_drawdown_pct": m.get("max_drawdown_pct"),
                         "target_r": bp.get("target_r"), "time_exit_tod": bp.get("time_exit_tod")})
        except Exception:  # noqa: BLE001
            pass
    return {"runs": runs}


@app.get("/api/backtest/runs/{rid}")
async def get_run(rid: str) -> dict:
    if rid == "realrun":
        rr = Path("data/realrun.json")
        return {"result": json.loads(rr.read_text())} if rr.exists() else {"result": None}
    f = _RUNS_DIR / f"{rid}.json"
    if f.exists():
        return {"result": json.loads(f.read_text()).get("result")}
    return {"result": None}


@app.delete("/api/backtest/runs/{rid}")
async def delete_run(rid: str) -> dict:
    f = _RUNS_DIR / f"{rid}.json"
    if rid != "realrun" and f.exists():
        f.unlink()
        return {"ok": True}
    return {"ok": False}


@app.get("/api/backtest/jobs")
async def list_jobs() -> dict:
    """All known jobs (running + recently finished) for the live panel."""
    jobs = []
    for jid, j in app.state.jobs.items():
        jobs.append({
            "id": jid, "status": j["status"], "elapsed": round(time.time() - j["started"], 1),
            "progress": round(j.get("progress", 0.0), 3), "params": j["params"],
            "error": j.get("error"),
        })
    jobs.sort(key=lambda x: x["elapsed"])
    return {"jobs": jobs}


def _massive_key() -> str:
    return os.environ.get("POLYGON_API_KEY", "") or load_config().polygon_api_key


def _run_real_backtest(p: dict, on_progress=None) -> dict:
    """Blocking: a real Massive-data backtest. Runs in a worker thread. First
    multi-year run fetches a lot (cached to disk after); re-runs replay fast."""
    from dataclasses import asdict

    from .backtest import Backtester, PolygonHistory
    from .backtest.engine import BacktestConfig
    from .backtest.review import breakdowns

    key = _massive_key()
    if not key:
        raise RuntimeError("no Massive/POLYGON_API_KEY configured on the server")
    universe = "active" if p["session"] == "intraday" else "gap"
    prov = PolygonHistory(key, days=p["days"], max_per_min=0, max_candidates_per_day=p["max_candidates"],
                          fetch_news=False, cache_dir="data/cache/polygon", universe_mode=universe)
    scan = ScanConfig(require_news=False, min_relative_volume=p["min_rvol"])
    bt = BacktestConfig(session=p["session"], target_r=p["target_r"], max_hold_minutes=p["max_hold"],
                        slippage_pct=p["slippage_pct"], premarket_slippage_pct=p["slippage_pct"],
                        time_exit_tod=p["time_exit_tod"])
    res = Backtester(prov, scan=scan, bt=bt).run(on_progress=on_progress)
    bd = breakdowns(res.trades)
    return {
        "synthetic": False, "feed": "massive", "session": p["session"], "days": res.days,
        "metrics": asdict(res.metrics), "equity_curve": res.equity_curve,
        "trades": [asdict(t) for t in res.trades], "monthly": bd["monthly"], "yearly": bd["yearly"],
    }


_MAX_CONCURRENT_JOBS = 3


async def _job_worker(job_id: str, params: dict) -> None:
    job = app.state.jobs[job_id]

    def on_progress(frac: float) -> None:
        job["progress"] = frac

    try:
        job["result"] = await asyncio.to_thread(_run_real_backtest, params, on_progress)
        job["status"] = "done"
        job["progress"] = 1.0
        _save_run("real", params, job["result"])
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(e)
    # drop the oldest finished jobs so the registry doesn't grow forever
    finished = [k for k, v in app.state.jobs.items() if v["status"] != "running"]
    for k in sorted(finished, key=lambda k: app.state.jobs[k]["started"])[:-20]:
        app.state.jobs.pop(k, None)


@app.post("/api/backtest/launch")
async def backtest_launch(session: str = "premarket", days: int = 252, target_r: float = 2.0,
                          slippage_pct: float = 0.5, max_hold: int = 60, time_exit_tod: int = 630,
                          min_rvol: float = 3.0, max_candidates: int = 20) -> dict:
    """Kick off a REAL Massive-data backtest in the background (so a multi-year
    run doesn't time out the request). Concurrent runs allowed up to a cap.
    Poll /api/backtest/job/{id} or the panel via /api/backtest/jobs."""
    if not _massive_key():
        return {"ok": False, "error": "no Massive key configured on the server"}
    running = sum(1 for j in app.state.jobs.values() if j["status"] == "running")
    if running >= _MAX_CONCURRENT_JOBS:
        return {"ok": False,
                "error": f"{running} running (max {_MAX_CONCURRENT_JOBS}) — wait for one to finish"}
    params = {
        "session": session if session in ("premarket", "intraday", "regular") else "premarket",
        "days": max(5, min(int(days), 1300)),   # up to ~5y
        "target_r": target_r, "slippage_pct": slippage_pct, "max_hold": max_hold,
        "time_exit_tod": int(time_exit_tod), "min_rvol": min_rvol,
        "max_candidates": max(1, min(int(max_candidates), 25)),
    }
    job_id = uuid.uuid4().hex[:12]
    app.state.jobs[job_id] = {"status": "running", "params": params, "started": time.time(),
                              "result": None, "error": None, "progress": 0.0}
    asyncio.create_task(_job_worker(job_id, params))
    return {"ok": True, "job_id": job_id, "params": params}


@app.get("/api/backtest/job/{job_id}")
async def backtest_job(job_id: str) -> dict:
    job = app.state.jobs.get(job_id)
    if job is None:
        return {"status": "unknown"}
    out = {"status": job["status"], "elapsed": round(time.time() - job["started"], 1),
           "params": job["params"]}
    if job["status"] == "done":
        out["result"] = job["result"]
    elif job["status"] == "error":
        out["error"] = job["error"]
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
