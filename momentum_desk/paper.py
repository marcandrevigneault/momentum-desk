"""Simulated paper-trading desk for the cockpit.

Holds open positions with an entry, a (ratcheting) trailing stop, and a
take-profit target; on every price tick it trails the stop up, and auto-exits
when the trailing stop or the target is hit. Realized and unrealized P&L are
always reported **net of modeled commissions**, so the dashboard's numbers
match what a real fill would cost. Sizing comes from the RiskEngine; closes feed
realized P&L back so the daily-loss circuit breaker reacts to paper results too.

This is the seam where the real broker (IBKR CP-Gateway, later) drops in: the
cockpit talks to PaperDesk; PaperDesk talks to a broker — SimBroker today.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from .broker import Order, OrderSide, OrderType, SimBroker
from .models import Snapshot
from .risk import RiskEngine


@dataclass
class OpenPosition:
    symbol: str
    qty: int
    entry: float
    stop: float            # the live (trailed) stop level
    init_stop: float
    target: float
    trail_pct: float
    high_water: float
    entry_commission: float
    opened_ts: float


@dataclass
class ClosedTrade:
    symbol: str
    qty: int
    entry: float
    exit: float
    exit_reason: str       # "target" | "trail" | "manual"
    gross_pnl: float
    commission: float
    pnl: float             # net of commission
    opened_ts: float
    closed_ts: float


class PaperDesk:
    def __init__(
        self,
        risk: RiskEngine,
        target_r: float = 2.0,
        trail_pct: float = 4.0,
        commission_per_share: float = 0.005,
        commission_min: float = 1.0,
        clock=time.time,
    ) -> None:
        self.risk = risk
        self.broker = SimBroker()
        self.target_r = target_r
        self.trail_pct = trail_pct
        self._cps = commission_per_share
        self._cmin = commission_min
        self._clock = clock
        self.open: dict[str, OpenPosition] = {}
        self.closed: list[ClosedTrade] = []

    def _commission(self, shares: int) -> float:
        return max(self._cmin, shares * self._cps)

    # ---- actions ----
    def open_position(self, snap: Snapshot, entry: float, stop: float) -> dict:
        if snap.symbol in self.open:
            return {"ok": False, "reasons": ["already in a position"]}
        plan = self.risk.plan(snap, entry=entry, stop=stop)
        if not plan.ok or plan.shares <= 0:
            return {"ok": False, "reasons": plan.reasons or ["rejected"]}
        self.broker.place_order(
            Order(snap.symbol, OrderSide.BUY, plan.shares, OrderType.LMT, limit_price=entry)
        )
        target = entry + self.target_r * (entry - stop)
        self.open[snap.symbol] = OpenPosition(
            symbol=snap.symbol, qty=plan.shares, entry=entry, stop=stop, init_stop=stop,
            target=round(target, 4), trail_pct=self.trail_pct, high_water=entry,
            entry_commission=round(self._commission(plan.shares), 2), opened_ts=self._clock(),
        )
        return {"ok": True, "symbol": snap.symbol, "shares": plan.shares,
                "entry": entry, "stop": stop, "target": round(target, 4)}

    def close_position(self, symbol: str, price: float, reason: str = "manual") -> ClosedTrade | None:
        pos = self.open.pop(symbol, None)
        if pos is None:
            return None
        self.broker.place_order(Order(symbol, OrderSide.SELL, pos.qty, OrderType.LMT, limit_price=price))
        exit_comm = self._commission(pos.qty)
        gross = (price - pos.entry) * pos.qty
        commission = round(pos.entry_commission + exit_comm, 2)
        pnl = round(gross - commission, 2)
        trade = ClosedTrade(
            symbol=symbol, qty=pos.qty, entry=pos.entry, exit=round(price, 4), exit_reason=reason,
            gross_pnl=round(gross, 2), commission=commission, pnl=pnl,
            opened_ts=pos.opened_ts, closed_ts=self._clock(),
        )
        self.closed.append(trade)
        self.risk.record_fill(pnl)   # paper results drive the daily-loss breaker
        return trade

    def update(self, prices: dict[str, float]) -> list[ClosedTrade]:
        """One tick: trail stops up and auto-exit on stop/target. Returns any
        trades closed this tick."""
        exits: list[ClosedTrade] = []
        for symbol in list(self.open):
            pos = self.open[symbol]
            price = prices.get(symbol)
            if price is None:
                continue
            pos.high_water = max(pos.high_water, price)
            trail = pos.high_water * (1 - pos.trail_pct / 100.0)
            pos.stop = max(pos.stop, round(trail, 4))   # ratchet up only
            if price <= pos.stop:
                exits.append(self.close_position(symbol, pos.stop, "trail"))  # type: ignore[arg-type]
            elif price >= pos.target:
                exits.append(self.close_position(symbol, pos.target, "target"))  # type: ignore[arg-type]
        return exits

    # ---- views ----
    def positions_view(self, prices: dict[str, float]) -> list[dict]:
        out = []
        for pos in self.open.values():
            last = prices.get(pos.symbol, pos.entry)
            est_exit_comm = self._commission(pos.qty)
            unreal = round((last - pos.entry) * pos.qty - pos.entry_commission - est_exit_comm, 2)
            out.append({
                "symbol": pos.symbol, "qty": pos.qty, "entry": pos.entry, "last": round(last, 4),
                "stop": pos.stop, "target": pos.target, "high_water": round(pos.high_water, 4),
                "unrealized_pnl": unreal,
            })
        return out

    def account_view(self, prices: dict[str, float]) -> dict:
        realized = round(sum(t.pnl for t in self.closed), 2)
        unrealized = round(sum(p["unrealized_pnl"] for p in self.positions_view(prices)), 2)
        start = self.risk.config.account_equity
        return {
            "equity": round(start + realized + unrealized, 2),
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "day_pnl": round(realized + unrealized, 2),
            "open_positions": len(self.open),
            "closed_trades": len(self.closed),
            "daily_loss_limit_hit": self.risk.daily_loss_limit_hit,
        }

    def trades_view(self) -> list[dict]:
        return [asdict(t) for t in reversed(self.closed)]   # most recent first
