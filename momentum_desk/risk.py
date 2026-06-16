"""The risk engine — the part that actually protects a self-aware losing trader.

It is mechanical on purpose: you set the limits once, when calm, and the engine
refuses to let in-the-moment emotion override them. Position size is derived
from your stop, never guessed. A daily-loss circuit breaker halts new entries.
And the liquidity guard answers your own diagnosis — it tells you when YOUR order
is too big for the tape, i.e. when you would become the exit liquidity.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .models import Snapshot


class Verdict(StrEnum):
    OK = "ok"
    REJECTED = "rejected"


@dataclass
class RiskConfig:
    account_equity: float = 25_000.0       # PDT-minimum-ish default; set to yours
    max_risk_per_trade_pct: float = 1.0    # % of equity risked between entry and stop
    max_daily_loss_pct: float = 3.0        # circuit breaker: stop trading for the day
    max_position_pct_of_equity: float = 25.0   # no single name dominates the book
    max_pct_of_recent_volume: float = 1.0      # your size vs tape — the liquidity guard
    min_stop_distance_pct: float = 1.0     # reject no-stop / too-tight "hope" trades
    compound: bool = False                 # size off CURRENT equity (the sim feeds it
                                           # back via mark_equity) vs a fixed % of the
                                           # starting balance — see RiskEngine.live_equity


@dataclass
class PositionPlan:
    symbol: str
    verdict: Verdict
    shares: int
    entry: float
    stop: float
    risk_dollars: float
    reasons: list[str]

    @property
    def ok(self) -> bool:
        return self.verdict is Verdict.OK


class RiskEngine:
    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()
        self.realized_pnl_today: float = 0.0
        # the equity sizing and the daily-loss breaker read. Starts at the
        # configured balance; when compound is on the sim calls mark_equity()
        # after each fill so risk scales with the live book.
        self.live_equity: float = self.config.account_equity

    def mark_equity(self, equity: float) -> None:
        """Sim loops call this after every fill. No-op unless compounding, so
        fixed-dollar runs (and their snapshots) are unchanged."""
        if self.config.compound:
            self.live_equity = equity

    @property
    def daily_loss_limit_hit(self) -> bool:
        limit = -self.live_equity * self.config.max_daily_loss_pct / 100.0
        return self.realized_pnl_today <= limit

    def record_fill(self, realized_pnl: float) -> None:
        """Feed closed-trade P&L back so the circuit breaker can trip."""
        self.realized_pnl_today += realized_pnl

    def plan(self, snap: Snapshot, entry: float, stop: float, side: str = "long") -> PositionPlan:
        """Size a trade from its stop, then run every guard. Returns a plan with
        a share count if OK, or verdict=REJECTED with the reasons why. `side`
        flips the stop geometry for short (mean-reversion fade) trades — the stop
        sits ABOVE entry there; the sizing math is otherwise identical."""
        c = self.config
        reasons: list[str] = []

        if self.daily_loss_limit_hit:
            reasons.append(f"daily loss limit hit ({c.max_daily_loss_pct}% of equity) — done for the day")
            return PositionPlan(snap.symbol, Verdict.REJECTED, 0, entry, stop, 0.0, reasons)

        stop_dist = (stop - entry) if side == "short" else (entry - stop)
        stop_dist_pct = 100.0 * stop_dist / entry if entry > 0 else 0.0
        if stop_dist <= 0:
            reasons.append(f"stop on wrong side of entry for a {side} (sizing needs a real risk distance)")
            return PositionPlan(snap.symbol, Verdict.REJECTED, 0, entry, stop, 0.0, reasons)
        if stop_dist_pct < c.min_stop_distance_pct:
            # ENFORCED (not advisory): a sub-floor stop ⇒ enormous share count and
            # inflated R-multiples — exactly the "hope" trade this guard exists to
            # refuse. Reject rather than silently size it.
            reasons.append(
                f"stop too tight ({stop_dist_pct:.1f}% < {c.min_stop_distance_pct}%) — noise will stop you out"
            )
            return PositionPlan(snap.symbol, Verdict.REJECTED, 0, entry, stop, 0.0, reasons)

        risk_dollars = self.live_equity * c.max_risk_per_trade_pct / 100.0
        shares = int(risk_dollars / stop_dist)

        # cap 1: position notional vs equity
        max_notional = self.live_equity * c.max_position_pct_of_equity / 100.0
        if shares * entry > max_notional:
            shares = int(max_notional / entry)
            reasons.append(f"size capped to {c.max_position_pct_of_equity}% of equity")

        # cap 2: THE liquidity guard — your size vs the tape so far today
        vol_cap = int(snap.cum_volume * c.max_pct_of_recent_volume / 100.0)
        if vol_cap > 0 and shares > vol_cap:
            reasons.append(
                f"you would be the liquidity: {shares} sh > {c.max_pct_of_recent_volume}% "
                f"of today's {snap.cum_volume:,} sh — size cut to {vol_cap}"
            )
            shares = vol_cap

        if shares <= 0:
            reasons.append("computed size is zero after guards — skip this trade")
            return PositionPlan(snap.symbol, Verdict.REJECTED, 0, entry, stop, 0.0, reasons)

        return PositionPlan(
            symbol=snap.symbol, verdict=Verdict.OK, shares=shares,
            entry=entry, stop=stop, risk_dollars=round(shares * stop_dist, 2),
            reasons=reasons,
        )
