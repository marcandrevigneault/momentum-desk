"""Dry-run bridge: drive the reconciled live engine from a Lab Strategy on real
bars, sizing each entry off the (paper) account exactly as the backtest does —
but transmitting NOTHING. This is the review surface before any live order.

`strategy_to_engine` maps a Lab Strategy → the engine's ScreenConfig + ExitPolicy.
`dryrun_day` replays one day's candidates through SymbolTrackers and returns the
intended orders (entry, stop, sized shares, exit, P&L). Because the engine is
provably identical to run_simulation (test_live_engine), these intended orders are
exactly what the strategy backtested — now produced the live way.
"""
from __future__ import annotations

from .edge.exits import ExitPolicy
from .edge.portfolio import _policy
from .edge.screen import ScreenConfig, _passes_gate
from .edge.strategy import Strategy
from .live_engine import EntrySignal, ExitSignal, SymbolTracker
from .models import Snapshot
from .risk import RiskConfig, RiskEngine

_COMMISSION_PER_SHARE = 0.005
_COMMISSION_MIN = 1.0


def strategy_to_engine(strategy: Strategy) -> tuple[ScreenConfig, ExitPolicy]:
    """Lab Strategy → the engine's entry screen + exit policy. Single-leg only
    (the per-symbol engine evaluates one entry/exit stream; combos are out)."""
    return ScreenConfig(session=strategy.session), _policy(strategy.exit_policy)


def supported(strategy: Strategy) -> bool:
    return strategy.kind == "single"


def dryrun_day(provider, day: str, strategy: Strategy, *, account_equity: float,
               slippage_pct: float = 0.3) -> list[dict]:
    """Replay one day through the reconciled engine for `strategy`; return the
    intended orders (sized off account_equity, the same way run_simulation sizes).
    Nothing is transmitted. Single-strategy only."""
    cfg, policy = strategy_to_engine(strategy)
    risk = RiskEngine(RiskConfig(account_equity=account_equity,
                                 max_risk_per_trade_pct=strategy.sizing.risk_pct,
                                 compound=(strategy.sizing.mode == "compound")))
    intended: list[dict] = []
    for cand in provider.candidates(day):
        if not _passes_gate(cand, cfg):
            continue
        bars = provider.minutes(cand.symbol, day)
        if not bars:
            continue
        tracker = SymbolTracker(cand, cfg, policy, slippage_pct)
        order: dict | None = None
        for bar in bars:
            sig = tracker.on_bar(bar)
            if isinstance(sig, EntrySignal):
                eb = tracker.bars[tracker.entry_idx]
                snap = Snapshot(symbol=cand.symbol, last=sig.entry, prev_close=cand.prev_close,
                                day_open=cand.day_open, vwap=eb.vwap, cum_volume=eb.cum_volume,
                                avg_volume_20d=cand.avg_volume_20d, float_shares=cand.float_shares)
                plan = risk.plan(snap, entry=sig.entry, stop=sig.stop)
                if not plan.ok or plan.shares <= 0:
                    break   # risk engine refuses — no order (as run_simulation skips)
                order = {"symbol": cand.symbol, "entry": round(sig.entry, 4), "stop": round(sig.stop, 4),
                         "shares": plan.shares, "entry_tod": sig.entry_tod,
                         "risk_dollars": plan.risk_dollars}
            elif isinstance(sig, ExitSignal) and order is not None:
                _close(order, sig)
                intended.append(order)
                order = None
        if order is not None:               # still open at day end → force time exit
            eod = tracker.end_of_day()
            if eod is not None:
                _close(order, eod)
                intended.append(order)
    return intended


def _close(order: dict, sig: ExitSignal) -> None:
    gross = (sig.exit_price - order["entry"]) * order["shares"]
    commission = 2.0 * max(_COMMISSION_MIN, order["shares"] * _COMMISSION_PER_SHARE)
    order.update(exit=round(sig.exit_price, 4), exit_tod=sig.exit_tod, reason=sig.reason,
                 r=round(sig.r, 3), pnl=round(gross - commission, 2))
