"""Drive the reconciled engine from the LIVE tape — produce the entries/exits the
active Lab strategy *would* take right now, transmitting NOTHING.

This is the streaming twin of ``dryrun.dryrun_day``: instead of replaying a
historical day, it registers candidates from live Snapshots, feeds each symbol's
closed MinuteBars through a ``SymbolTracker``, and sizes every entry off the
account exactly as the backtest does. Because it reuses the same engine and the
same sizing bridge, the intended orders it logs are precisely what the strategy
backtested — now computed on the live feed. The trader loop above decides whether
to ever transmit them (it does not, until explicitly armed).
"""
from __future__ import annotations

from .backtest.data import DayCandidate, MinuteBar
from .dryrun import _close, strategy_to_engine, supported
from .edge.screen import _passes_gate
from .edge.strategy import Strategy
from .live_engine import EntrySignal, ExitSignal, SymbolTracker
from .models import Snapshot
from .risk import RiskConfig, RiskEngine

_INTENT_CAP = 500


def candidate_from_snapshot(snap: Snapshot, day: str) -> DayCandidate:
    """The pre-open context the engine gates/sizes on, lifted from a live snapshot
    (day_open/prev_close/avg_volume_20d are stable through the session)."""
    return DayCandidate(symbol=snap.symbol, day=day, prev_close=snap.prev_close,
                        day_open=snap.day_open, avg_volume_20d=snap.avg_volume_20d,
                        float_shares=snap.float_shares, has_news=snap.has_news,
                        news_headline=snap.news_headline)


class LiveEngine:
    """Per-symbol streaming entry/exit for one strategy, sized off the account.

    Feed it candidates (``observe``) and closed bars (``on_bar``); it accumulates
    ``closed`` intended orders and a human-facing ``intent`` log. Single-leg only.
    """

    def __init__(self, strategy: Strategy, *, account_equity: float, day: str,
                 slippage_pct: float = 0.3) -> None:
        if not supported(strategy):
            raise ValueError("LiveEngine supports single-leg strategies only")
        self.strategy = strategy
        self.day = day
        self.equity = account_equity
        self.cfg, self.policy = strategy_to_engine(strategy)
        self.slip = slippage_pct
        self.risk = RiskEngine(RiskConfig(
            account_equity=account_equity,
            max_risk_per_trade_pct=strategy.sizing.risk_pct,
            compound=(strategy.sizing.mode == "compound")))
        self.trackers: dict[str, SymbolTracker] = {}
        self.cands: dict[str, DayCandidate] = {}
        self.open: dict[str, dict] = {}      # symbol -> intended open order
        self.closed: list[dict] = []         # completed intended orders (== dryrun)
        self.intent: list[dict] = []         # rolling log: register/entry/exit/reject

    # ---- inputs -----------------------------------------------------------

    def observe(self, snap: Snapshot) -> None:
        """Register a symbol as a candidate if it passes the gate (idempotent)."""
        self.register(candidate_from_snapshot(snap, self.day))

    def register(self, cand: DayCandidate) -> None:
        if cand.symbol in self.trackers or not _passes_gate(cand, self.cfg):
            return
        self.cands[cand.symbol] = cand
        self.trackers[cand.symbol] = SymbolTracker(cand, self.cfg, self.policy, self.slip)
        self._log("watch", cand.symbol)

    def on_bar(self, symbol: str, bar: MinuteBar) -> dict | None:
        tr = self.trackers.get(symbol)
        if tr is None:
            return None
        sig = tr.on_bar(bar)
        if isinstance(sig, EntrySignal):
            return self._enter(symbol, sig, tr)
        if isinstance(sig, ExitSignal):
            return self._exit(symbol, sig)
        return None

    def finalize(self) -> None:
        """Force time-exits on anything still open (session close)."""
        for symbol in list(self.open):
            eod = self.trackers[symbol].end_of_day()
            if eod is not None:
                self._exit(symbol, eod)

    # ---- views ------------------------------------------------------------

    def snapshot(self) -> dict:
        return {
            "strategy": self.strategy.name, "day": self.day, "equity": self.equity,
            "session": self.cfg.session, "exit_policy": self.strategy.exit_policy,
            "watching": sorted(self.trackers),
            "holding": [dict(o) for o in self.open.values()],
            "closed": list(self.closed),
            "day_pnl": round(sum(o.get("pnl", 0.0) for o in self.closed), 2),
            "intent": self.intent[-100:],
        }

    # ---- internals --------------------------------------------------------

    def _enter(self, symbol: str, sig: EntrySignal, tr: SymbolTracker) -> dict:
        cand = self.cands[symbol]
        eb = tr.bars[tr.entry_idx]
        snap = Snapshot(symbol=symbol, last=sig.entry, prev_close=cand.prev_close,
                        day_open=cand.day_open, vwap=eb.vwap, cum_volume=eb.cum_volume,
                        avg_volume_20d=cand.avg_volume_20d, float_shares=cand.float_shares)
        plan = self.risk.plan(snap, entry=sig.entry, stop=sig.stop)
        if not plan.ok or plan.shares <= 0:
            tr.state = "done"                # risk refuses — no order (mirrors dryrun)
            rec = self._log("reject", symbol, reasons=plan.reasons)
            return rec
        order = {"symbol": symbol, "entry": round(sig.entry, 4), "stop": round(sig.stop, 4),
                 "shares": plan.shares, "entry_tod": sig.entry_tod,
                 "risk_dollars": plan.risk_dollars}
        self.open[symbol] = order
        self._log("entry", **order)
        return order

    def _exit(self, symbol: str, sig: ExitSignal) -> dict | None:
        order = self.open.pop(symbol, None)
        if order is None:
            return None
        _close(order, sig)                   # adds exit/exit_tod/reason/r/pnl
        self.closed.append(order)
        self._log("exit", **order)
        return order

    def _log(self, kind: str, symbol: str, **extra) -> dict:
        rec = {"kind": kind, "symbol": symbol, **extra}
        self.intent.append(rec)
        if len(self.intent) > _INTENT_CAP:
            del self.intent[: len(self.intent) - _INTENT_CAP]
        return rec
