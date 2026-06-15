"""End-to-end account simulation: the full assembled setup, run like a real book.

The screener/exit-lab/gauntlet all work in per-trade R, which deliberately
ignores capital. This does the opposite — it simulates an actual account:

  * detect candidates day by day (the session's entry trigger),
  * SIZE each by the live RiskEngine (risk-per-trade, the liquidity guard so you
    don't become the exit liquidity, the per-name notional cap),
  * respect a max number of CONCURRENT positions and the cash you actually have
    (you can't take every signal — capacity is a real constraint),
  * exit on the chosen policy (the 10% trailing stop by default),
  * honour the daily-loss circuit breaker, commissions and slippage,

and produce a real equity curve, drawdown, and trade log. This is the number
that matters: what the strategy would have done to a $25k account.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..backtest.data import Trade
from ..backtest.engine import Backtester
from ..models import Snapshot
from ..risk import RiskConfig, RiskEngine
from .exits import POLICIES, ExitPolicy, simulate_exit_detail
from .screen import ScreenConfig, _find_event, _passes_gate


@dataclass
class SimConfig:
    session: str = "intraday"
    exit_policy: str = "pct_trail_10"
    slippage_pct: float = 0.3
    max_concurrent: int = 5            # positions held at once (you watch a handful)
    max_gross_pct: float = 100.0       # sum of open notional ≤ this % of equity (cash acct)
    commission_per_share: float = 0.005
    commission_min: float = 1.0


@dataclass
class SimTrade:
    day: str
    symbol: str
    entry_tod: int
    exit_tod: int
    entry: float
    exit: float
    shares: int
    pnl: float
    r_multiple: float
    exit_reason: str


@dataclass
class PeriodRow:
    period: str
    trades: int
    wins: int
    win_rate: float
    pnl: float
    cum_pnl: float


@dataclass
class SimResult:
    session: str
    exit_policy: str
    days: int
    starting_equity: float
    final_equity: float
    n_signals: int          # how many entries triggered
    n_taken: int            # how many we had capacity/capital to take
    n_skipped_capacity: int
    metrics: dict = field(default_factory=dict)
    equity_curve: list[float] = field(default_factory=list)
    daily_equity: list[dict] = field(default_factory=list)
    monthly: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)


def _policy(name: str) -> ExitPolicy:
    for p in POLICIES:
        if p.name == name:
            return p
    raise ValueError(f"unknown exit policy {name!r}")


def _monthly(trades: list[SimTrade]) -> list[dict]:
    by_month: dict[str, list[SimTrade]] = {}
    for t in trades:
        by_month.setdefault(t.day[:7], []).append(t)
    rows, cum = [], 0.0
    for m in sorted(by_month):
        ts = by_month[m]
        pnl = sum(t.pnl for t in ts)
        cum += pnl
        wins = sum(1 for t in ts if t.pnl > 0)
        rows.append(asdict(PeriodRow(period=m, trades=len(ts), wins=wins,
                                     win_rate=round(100 * wins / len(ts), 1),
                                     pnl=round(pnl, 2), cum_pnl=round(cum, 2))))
    return rows


def _book_due(open_pos: list[dict], before_tod: int) -> tuple[list[dict], list[dict]]:
    """Split open positions into (those whose exit already happened, in time
    order) and (those still open) at a given time-of-day."""
    due = sorted((o for o in open_pos if o["exit_tod"] <= before_tod), key=lambda o: o["exit_tod"])
    rest = [o for o in open_pos if o["exit_tod"] > before_tod]
    return due, rest


def run_simulation(provider, scfg: SimConfig | None = None,
                   risk_cfg: RiskConfig | None = None) -> SimResult:
    scfg = scfg or SimConfig()
    risk_cfg = risk_cfg or RiskConfig()
    policy = _policy(scfg.exit_policy)
    screen = ScreenConfig(session=scfg.session)

    risk = RiskEngine(risk_cfg)
    equity = risk_cfg.account_equity
    curve = [equity]
    trades: list[SimTrade] = []
    daily: list[dict] = []
    n_signals = n_taken = n_skip = 0
    days = provider.trading_days()

    for day in days:
        risk.realized_pnl_today = 0.0  # fresh circuit breaker each session

        # 1) gather the day's potential trades (entry + precomputed exit fill)
        pots = []
        for cand in provider.candidates(day):
            if not _passes_gate(cand, screen):
                continue
            bars = provider.minutes(cand.symbol, day)
            if not bars:
                continue
            ev = _find_event(bars, screen)
            if ev is None:
                continue
            entry_idx, entry, stop, fwd = ev
            if entry - stop <= 0 or not fwd:
                continue
            eb = bars[entry_idx]
            fill = simulate_exit_detail(entry, stop, bars[: entry_idx + 1], fwd, policy, scfg.slippage_pct)
            snap = Snapshot(symbol=cand.symbol, last=entry, prev_close=cand.prev_close,
                            day_open=cand.day_open, vwap=eb.vwap, cum_volume=eb.cum_volume,
                            avg_volume_20d=cand.avg_volume_20d, float_shares=cand.float_shares)
            pots.append((eb.tod, entry, stop, fill, snap, cand.symbol))
            n_signals += 1
        pots.sort(key=lambda x: x[0])   # chronological by entry time-of-day

        # 2) event-driven intraday portfolio with concurrency + capital caps
        open_pos: list[dict] = []
        for (etod, entry, stop, fill, snap, sym) in pots:
            due, open_pos = _book_due(open_pos, etod)          # free slots for exits already past
            for op in due:
                equity += op["pnl"]
                risk.record_fill(op["pnl"])
                curve.append(round(equity, 2))
            if risk.daily_loss_limit_hit:
                continue
            if len(open_pos) >= scfg.max_concurrent:
                n_skip += 1
                continue
            plan = risk.plan(snap, entry=entry, stop=stop)
            if not plan.ok or plan.shares <= 0:
                continue
            notional = plan.shares * entry
            gross_open = sum(o["notional"] for o in open_pos)
            if gross_open + notional > equity * scfg.max_gross_pct / 100.0:
                n_skip += 1
                continue
            gross = (fill.exit_price - entry) * plan.shares
            commission = 2.0 * max(scfg.commission_min, plan.shares * scfg.commission_per_share)
            pnl = gross - commission
            r = pnl / plan.risk_dollars if plan.risk_dollars > 0 else 0.0
            trades.append(SimTrade(day=day, symbol=sym, entry_tod=etod, exit_tod=fill.exit_tod,
                                   entry=round(entry, 4), exit=round(fill.exit_price, 4),
                                   shares=plan.shares, pnl=round(pnl, 2), r_multiple=round(r, 3),
                                   exit_reason=fill.reason))
            n_taken += 1
            open_pos.append({"exit_tod": fill.exit_tod, "pnl": pnl, "notional": notional})

        due, _ = _book_due(open_pos, 10_000)   # close everything still open at end of day
        for op in due:
            equity += op["pnl"]
            risk.record_fill(op["pnl"])
            curve.append(round(equity, 2))
        daily.append({"date": day, "equity": round(equity, 2)})

    # metrics via the same honest reporter the backtester uses
    bt_trades = [Trade(symbol=t.symbol, day=t.day, entry_t=t.entry_tod, entry=t.entry, stop=0.0,
                       target=0.0, shares=t.shares, exit_t=t.exit_tod, exit=t.exit, pnl=t.pnl,
                       r_multiple=t.r_multiple, exit_reason=t.exit_reason) for t in trades]
    metrics = asdict(Backtester._metrics(bt_trades, curve, risk_cfg.account_equity))

    return SimResult(
        session=scfg.session, exit_policy=scfg.exit_policy, days=len(days),
        starting_equity=risk_cfg.account_equity, final_equity=round(equity, 2),
        n_signals=n_signals, n_taken=n_taken, n_skipped_capacity=n_skip,
        metrics=metrics, equity_curve=curve, daily_equity=daily,
        monthly=_monthly(trades), trades=[asdict(t) for t in trades],
    )
