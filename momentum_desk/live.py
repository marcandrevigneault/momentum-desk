"""Autonomous paper-trading loop — the assembled strategy, routed to a broker.

This is the module that can place orders, so it is conservative by construction:

  * **Dry-run by default.** Nothing transmits unless you pass a non-dry broker.
  * **Hard caps, checked every step:** max concurrent positions, max trades/day,
    one entry per symbol per day, and a session WINDOW (no entries before the
    open or after the cutoff).
  * **Broker-managed protective exit.** Every entry is paired with a trailing
    stop placed AT the broker (the 10% trail the exit-lab favoured), so the
    protection survives even if this loop dies.
  * **Flatten / kill switch.** `flatten()` market-closes everything; the CLI wires
    it to Ctrl-C and to a wall-clock flatten time.

It only ever sends what the RiskEngine sized — it never invents a quantity.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from .broker.base import Order, OrderSide, OrderType, route_plan
from .risk import RiskEngine
from .scanner import ScannerEngine
from .sizing import SizingConfig

_ET = ZoneInfo("America/New_York")


@dataclass
class LiveConfig:
    trail_pct: float = 10.0            # broker-side trailing stop (also the sizing stop distance)
    max_concurrent: int = 5
    max_trades_day: int = 20
    session_start_tod: int = 570       # 09:30 ET — no entries before
    session_end_tod: int = 660         # 11:00 ET — no NEW entries after
    flatten_tod: int = 720             # 12:00 ET — close everything
    poll_interval_s: int = 30


@dataclass
class LiveState:
    held: set[str] = field(default_factory=set)
    traded_today: set[str] = field(default_factory=set)
    trade_count: int = 0
    halted: bool = False
    log: list[dict] = field(default_factory=list)


def _et_tod(now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now(tz=_ET)
    now = now.astimezone(_ET)
    return now.hour * 60 + now.minute


class LivePaperTrader:
    """Polls the scanner, routes actionable signals to the broker under hard caps.
    `now_fn` returns the current ET minute-of-day (injectable for tests)."""

    def __init__(self, adapter, scanner: ScannerEngine, risk: RiskEngine, broker,
                 cfg: LiveConfig | None = None, sizing: SizingConfig | None = None,
                 now_fn=_et_tod) -> None:
        self.adapter = adapter
        self.scanner = scanner
        self.risk = risk
        self.broker = broker
        self.cfg = cfg or LiveConfig()
        self.sizing = sizing or SizingConfig()
        self.now_fn = now_fn
        self.state = LiveState()

    def _sizing_stop(self, last: float) -> float:
        # size off the trail distance, so risk-per-share matches the actual stop
        return last * (1 - self.cfg.trail_pct / 100.0)

    def _apply_sizing(self) -> None:
        """Size off the LIVE book each step. We set the risk engine's equity to
        the broker's current NAV, so even a *fixed* risk-% is a % of the current
        account — it compounds as the book grows (the "% equity" mode). nav-kelly
        additionally sets the risk-% from fractional Kelly. If the broker can't
        report NAV (None), equity is left at its configured value as a safe
        fallback."""
        nav_fn = getattr(self.broker, "nav", None)
        nav = nav_fn() if callable(nav_fn) else None
        if nav and nav > 0:
            self.risk.config.account_equity = nav
            if self.sizing.mode == "nav-kelly":
                self.risk.config.max_risk_per_trade_pct = self.sizing.risk_pct()

    def _sync_positions(self) -> None:
        """Drop any held symbol the broker no longer has (stopped out) so its
        concurrency slot frees up."""
        live = {p.symbol for p in self.broker.positions() if p.quantity != 0}
        self.state.held &= live

    def step(self, now_tod: int | None = None) -> dict:
        tod = now_tod if now_tod is not None else self.now_fn()
        self._sync_positions()
        self._apply_sizing()   # nav-kelly retunes risk to live NAV; fixed is a no-op

        # wall-clock flatten / end-of-window
        if tod >= self.cfg.flatten_tod and self.broker.positions():
            return {"tod": tod, "flattened": self.flatten(), "held": sorted(self.state.held)}

        acted: list[dict] = []
        if self.state.halted or not (self.cfg.session_start_tod <= tod < self.cfg.session_end_tod):
            return {"tod": tod, "acted": acted, "held": sorted(self.state.held),
                    "trades": self.state.trade_count, "reason": "halted/out-of-window"}

        for snap in self.adapter.poll():
            sig = self.scanner.evaluate(snap)
            if sig is None or not sig.actionable:
                continue
            sym = sig.symbol
            if sym in self.state.held or sym in self.state.traded_today:
                continue
            if len(self.state.held) >= self.cfg.max_concurrent:
                continue
            if self.state.trade_count >= self.cfg.max_trades_day:
                continue
            if self.risk.daily_loss_limit_hit:
                self.state.halted = True
                break
            # conviction sizing: risk scales with this signal's score (the
            # "more evident" trades get more — capped). Other modes are per-step.
            if self.sizing.mode == "conviction":
                self.risk.config.max_risk_per_trade_pct = self.sizing.conviction_risk_pct(sig.score)
            risk_pct = self.risk.config.max_risk_per_trade_pct
            plan = self.risk.plan(snap, entry=snap.last, stop=self._sizing_stop(snap.last))
            if not plan.ok or plan.shares <= 0:
                continue
            results = route_plan(self.broker, plan, ref_price=snap.last, trail_pct=self.cfg.trail_pct)
            self.state.held.add(sym)
            self.state.traded_today.add(sym)
            self.state.trade_count += 1
            rec = {"tod": tod, "symbol": sym, "shares": plan.shares, "entry": round(snap.last, 4),
                   "score": round(sig.score, 1), "risk_pct": round(risk_pct, 2),
                   "trail_pct": self.cfg.trail_pct, "results": [r.status for r in results]}
            acted.append(rec)
            self.state.log.append(rec)

        return {"tod": tod, "acted": acted, "held": sorted(self.state.held),
                "trades": self.state.trade_count}

    def flatten(self) -> list[str]:
        """Market-close every open position — the kill switch."""
        closed = []
        for p in self.broker.positions():
            if p.quantity == 0:
                continue
            side = OrderSide.SELL if p.quantity > 0 else OrderSide.BUY
            self.broker.place_order(
                Order(symbol=p.symbol, side=side, quantity=abs(p.quantity), type=OrderType.MKT),
                ref_price=p.avg_price,
            )
            closed.append(p.symbol)
        self.state.held.clear()
        return closed
