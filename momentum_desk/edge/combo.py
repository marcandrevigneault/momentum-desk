"""Combo strategies — run several strategy 'legs' in ONE shared-capital book.

A single edge concentrates risk: same names, same regime, same time of day. A
combo runs multiple legs (e.g. the pre-market gap play AND the intraday HOD-break
play, or an aggressive and a conservative variant) into one account that shares
equity, the concurrency cap and the daily-loss breaker. If the legs aren't
perfectly correlated, the book's risk-adjusted return beats any single leg —
that's the whole point of combining them.

This reuses the portfolio engine's helpers read-only; per-leg P&L is attributed
so you can see what each contributes and whether they actually diversify.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..backtest.data import HistoricalProvider, Trade
from ..backtest.engine import Backtester
from ..models import Snapshot
from ..risk import RiskConfig, RiskEngine
from .exits import simulate_exit_detail
from .portfolio import SimTrade, _book_due, _monthly, _policy
from .screen import ScreenConfig, _find_event, _passes_gate


@dataclass
class ComboLeg:
    name: str
    provider: HistoricalProvider
    session: str
    exit_policy: str = "pct_trail_10"
    slippage_pct: float = 0.3
    max_ext_pct: float | None = None    # optional entry filters (from the screen findings)
    rvol_max: float | None = None


@dataclass
class ComboConfig:
    max_concurrent: int = 5
    max_gross_pct: float = 100.0
    commission_per_share: float = 0.005
    commission_min: float = 1.0


@dataclass
class ComboResult:
    legs: list[str]
    days: int
    starting_equity: float
    final_equity: float
    n_signals: int
    n_taken: int
    n_skipped_capacity: int
    metrics: dict = field(default_factory=dict)
    leg_pnl: dict = field(default_factory=dict)      # per-leg attribution ($)
    leg_trades: dict = field(default_factory=dict)   # per-leg trade count
    equity_curve: list[float] = field(default_factory=list)
    daily_equity: list[dict] = field(default_factory=list)
    monthly: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)


def _leg_day_pots(leg: ComboLeg, day: str) -> list[tuple]:
    """A leg's potential trades for one day: (entry_tod, entry, stop, fill, snap, symbol, leg)."""
    screen = ScreenConfig(session=leg.session)
    policy = _policy(leg.exit_policy)
    pots = []
    for cand in leg.provider.candidates(day):
        if not _passes_gate(cand, screen):
            continue
        bars = leg.provider.minutes(cand.symbol, day)
        if not bars:
            continue
        ev = _find_event(bars, screen)
        if ev is None:
            continue
        entry_idx, entry, stop, fwd = ev
        if entry - stop <= 0 or not fwd:
            continue
        eb = bars[entry_idx]
        ext = 100.0 * (entry - eb.vwap) / eb.vwap if eb.vwap > 0 else 0.0
        rvol = eb.cum_volume / cand.avg_volume_20d if cand.avg_volume_20d > 0 else 0.0
        if leg.max_ext_pct is not None and ext > leg.max_ext_pct:
            continue
        if leg.rvol_max is not None and rvol > leg.rvol_max:
            continue
        fill = simulate_exit_detail(entry, stop, bars[: entry_idx + 1], fwd, policy, leg.slippage_pct)
        snap = Snapshot(symbol=cand.symbol, last=entry, prev_close=cand.prev_close,
                        day_open=cand.day_open, vwap=eb.vwap, cum_volume=eb.cum_volume,
                        avg_volume_20d=cand.avg_volume_20d, float_shares=cand.float_shares)
        pots.append((eb.tod, entry, stop, fill, snap, cand.symbol, leg.name))
    return pots


def run_combo(legs: list[ComboLeg], ccfg: ComboConfig | None = None,
              risk_cfg: RiskConfig | None = None) -> ComboResult:
    ccfg = ccfg or ComboConfig()
    risk_cfg = risk_cfg or RiskConfig()
    risk = RiskEngine(risk_cfg)
    equity = risk_cfg.account_equity
    curve = [equity]
    trades: list[SimTrade] = []
    daily: list[dict] = []
    leg_pnl = {leg.name: 0.0 for leg in legs}
    leg_n = {leg.name: 0 for leg in legs}
    n_signals = n_taken = n_skip = 0

    all_days = sorted(set().union(*[set(leg.provider.trading_days()) for leg in legs]))
    for day in all_days:
        risk.realized_pnl_today = 0.0
        pots = []
        for leg in legs:
            pots.extend(_leg_day_pots(leg, day))
        n_signals += len(pots)
        pots.sort(key=lambda x: x[0])   # chronological across all legs

        open_pos: list[dict] = []
        for (etod, entry, stop, fill, snap, sym, legname) in pots:
            due, open_pos = _book_due(open_pos, etod)
            for op in due:
                equity += op["pnl"]
                risk.record_fill(op["pnl"])
                leg_pnl[op["leg"]] += op["pnl"]
                curve.append(round(equity, 2))
            if risk.daily_loss_limit_hit:
                continue
            if len(open_pos) >= ccfg.max_concurrent:
                n_skip += 1
                continue
            plan = risk.plan(snap, entry=entry, stop=stop)
            if not plan.ok or plan.shares <= 0:
                continue
            notional = plan.shares * entry
            if sum(o["notional"] for o in open_pos) + notional > equity * ccfg.max_gross_pct / 100.0:
                n_skip += 1
                continue
            gross = (fill.exit_price - entry) * plan.shares
            commission = 2.0 * max(ccfg.commission_min, plan.shares * ccfg.commission_per_share)
            pnl = gross - commission
            r = pnl / plan.risk_dollars if plan.risk_dollars > 0 else 0.0
            trades.append(SimTrade(day=day, symbol=f"{sym}·{legname}", entry_tod=etod, exit_tod=fill.exit_tod,
                                   entry=round(entry, 4), exit=round(fill.exit_price, 4), shares=plan.shares,
                                   pnl=round(pnl, 2), r_multiple=round(r, 3), exit_reason=fill.reason))
            n_taken += 1
            leg_n[legname] += 1
            open_pos.append({"exit_tod": fill.exit_tod, "pnl": pnl, "notional": notional, "leg": legname})

        due, _ = _book_due(open_pos, 10_000)
        for op in due:
            equity += op["pnl"]
            risk.record_fill(op["pnl"])
            leg_pnl[op["leg"]] += op["pnl"]
            curve.append(round(equity, 2))
        daily.append({"date": day, "equity": round(equity, 2)})

    bt_trades = [Trade(symbol=t.symbol, day=t.day, entry_t=t.entry_tod, entry=t.entry, stop=0.0,
                       target=0.0, shares=t.shares, exit_t=t.exit_tod, exit=t.exit, pnl=t.pnl,
                       r_multiple=t.r_multiple, exit_reason=t.exit_reason) for t in trades]
    metrics = asdict(Backtester._metrics(bt_trades, curve, risk_cfg.account_equity))

    return ComboResult(
        legs=[leg.name for leg in legs], days=len(all_days),
        starting_equity=risk_cfg.account_equity, final_equity=round(equity, 2),
        n_signals=n_signals, n_taken=n_taken, n_skipped_capacity=n_skip, metrics=metrics,
        leg_pnl={k: round(v, 2) for k, v in leg_pnl.items()}, leg_trades=leg_n,
        equity_curve=curve, daily_equity=daily, monthly=_monthly(trades),
        trades=[asdict(t) for t in trades],
    )
