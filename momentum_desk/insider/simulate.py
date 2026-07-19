"""Multi-day daily-bar portfolio simulator for the insider-buying strategy.

This is the daily-bar sibling of ``edge/portfolio.py``'s intraday account
simulator: same shared-capital book (capacity, gross cap, commissions,
compounding), same ``AccountRun``/metrics contract, but the exit ladder is
priced off daily OHLC bars over many trading days instead of one session's
minute bars — hard stop, then a trailing stop off the highest close since
entry, then a time stop, then a forced close at the end of the data window.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..backtest.data import Trade
from ..backtest.metrics import compute_metrics
from ..edge.result import AccountRun
from ..risk import RiskConfig
from .models import InsiderConfig, InsiderEvent
from .prices import DailyBar, DailyProvider

_ENTRY_TOD = 570   # 09:30 ET — entries fill at the open
_EXIT_TOD = 960    # 16:00 ET — exits fill at the close (or intraday for stops)


@dataclass
class InsiderResult(AccountRun):
    config: dict = field(default_factory=dict)   # the InsiderConfig used


@dataclass
class InsiderTrade:
    day: str          # entry day (keeps LabPage/date-scrubber contract)
    exit_day: str
    symbol: str
    entry_tod: int    # constant 570 (open) — keeps SimTrade-shaped consumers happy
    exit_tod: int     # constant 960 (close)
    entry: float
    exit: float
    shares: int
    pnl: float
    r_multiple: float
    exit_reason: str  # "stop" | "trail" | "time" | "end"
    hold_days: int


@dataclass
class _PeriodRow:
    period: str
    trades: int
    wins: int
    win_rate: float
    pnl: float
    cum_pnl: float


def _monthly(trades: list[InsiderTrade]) -> list[dict]:
    """Same grouping shape as edge/portfolio.py's ``_monthly`` — copied (not
    imported, it's private there) and grouped by the ENTRY day's year-month."""
    by_month: dict[str, list[InsiderTrade]] = {}
    for t in trades:
        by_month.setdefault(t.day[:7], []).append(t)
    rows, cum = [], 0.0
    for m in sorted(by_month):
        ts = by_month[m]
        pnl = sum(t.pnl for t in ts)
        cum += pnl
        wins = sum(1 for t in ts if t.pnl > 0)
        rows.append(asdict(_PeriodRow(period=m, trades=len(ts), wins=wins,
                                       win_rate=round(100 * wins / len(ts), 1),
                                       pnl=round(pnl, 2), cum_pnl=round(cum, 2))))
    return rows


class _OpenPosition:
    __slots__ = ("symbol", "entry_day", "entry_index", "entry", "stop", "shares",
                 "risk_dollars", "high_close")

    def __init__(self, symbol: str, entry_day: str, entry_index: int, entry: float,
                 stop: float, shares: int, risk_dollars: float, high_close: float) -> None:
        self.symbol = symbol
        self.entry_day = entry_day
        self.entry_index = entry_index
        self.entry = entry
        self.stop = stop
        self.shares = shares
        self.risk_dollars = risk_dollars
        self.high_close = high_close


def run_insider(events: list[InsiderEvent], provider: DailyProvider,
                cfg: InsiderConfig, risk_cfg: RiskConfig,
                *, max_concurrent: int = 5, max_gross_pct: float = 100.0,
                slippage_pct: float = 0.3,
                commission_per_share: float = 0.005, commission_min: float = 1.0) -> InsiderResult:
    days = provider.trading_days()
    day_index = {d: i for i, d in enumerate(days)}
    last_day = days[-1] if days else None

    events_by_day: dict[str, list[InsiderEvent]] = {}
    for ev in events:
        events_by_day.setdefault(ev.trigger_day, []).append(ev)

    bars_cache: dict[str, dict[str, DailyBar]] = {}

    def bars_for(symbol: str) -> dict[str, DailyBar]:
        cached = bars_cache.get(symbol)
        if cached is None:
            cached = {b.day: b for b in provider.daily(symbol)}
            bars_cache[symbol] = cached
        return cached

    equity = risk_cfg.account_equity
    curve = [round(equity, 2)]
    trades: list[InsiderTrade] = []
    daily_equity: list[dict] = []
    open_pos: list[_OpenPosition] = []
    n_signals = n_taken = n_skip = 0

    def _close(pos: _OpenPosition, exit_day: str, exit_price: float, reason: str) -> None:
        nonlocal equity
        gross = (exit_price - pos.entry) * pos.shares
        commission = 2.0 * max(commission_min, pos.shares * commission_per_share)
        pnl = gross - commission
        r = pnl / pos.risk_dollars if pos.risk_dollars > 0 else 0.0
        hold = day_index[exit_day] - pos.entry_index
        trades.append(InsiderTrade(
            day=pos.entry_day, exit_day=exit_day, symbol=pos.symbol,
            entry_tod=_ENTRY_TOD, exit_tod=_EXIT_TOD,
            entry=round(pos.entry, 4), exit=round(exit_price, 4),
            shares=pos.shares, pnl=round(pnl, 2), r_multiple=round(r, 3),
            exit_reason=reason, hold_days=hold,
        ))
        equity += pnl
        curve.append(round(equity, 2))

    for day in days:
        idx = day_index[day]
        is_last = day == last_day

        # 1) exits, in priority order, for every already-open position
        still_open: list[_OpenPosition] = []
        for pos in open_pos:
            bar = bars_for(pos.symbol).get(day)
            if bar is None:
                still_open.append(pos)
                continue

            exit_price = None
            reason = ""
            if bar.o <= pos.stop:
                exit_price, reason = bar.o, "stop"
            elif bar.l <= pos.stop:
                exit_price, reason = pos.stop, "stop"
            else:
                trail = pos.high_close * (1 - cfg.trail_pct / 100)
                if bar.o <= trail:
                    exit_price, reason = bar.o, "trail"
                elif bar.l <= trail:
                    exit_price, reason = trail, "trail"
                else:
                    held = idx - pos.entry_index
                    if held == cfg.hold_days:
                        exit_price, reason = bar.c, "time"

            if exit_price is not None:
                _close(pos, day, exit_price, reason)
            else:
                pos.high_close = max(pos.high_close, bar.c)
                still_open.append(pos)
        open_pos = still_open

        # 2) entries — same-day events processed in input order
        for ev in events_by_day.get(day, []):
            bar = bars_for(ev.symbol).get(day)
            if bar is None:
                continue   # no bar for this symbol on its trigger day: not a signal
            n_signals += 1

            if len(open_pos) >= max_concurrent:
                n_skip += 1
                continue

            entry = bar.o * (1 + slippage_pct / 100)
            stop = entry * (1 - cfg.stop_pct / 100)
            dist = entry - stop
            if dist <= 0:
                continue

            equity_base = equity if risk_cfg.compound else risk_cfg.account_equity
            risk_dollars = equity_base * risk_cfg.max_risk_per_trade_pct / 100
            shares = int(risk_dollars / dist)

            max_notional = equity_base * risk_cfg.max_position_pct_of_equity / 100.0
            if shares * entry > max_notional:
                shares = int(max_notional / entry)
            if shares <= 0:
                continue

            notional = shares * entry
            gross_open = sum(p.shares * p.entry for p in open_pos)
            if gross_open + notional > equity_base * max_gross_pct / 100:
                n_skip += 1
                continue

            open_pos.append(_OpenPosition(
                symbol=ev.symbol, entry_day=day, entry_index=idx, entry=entry,
                stop=stop, shares=shares, risk_dollars=risk_dollars, high_close=bar.c,
            ))
            n_taken += 1

        # 3) final-day closures: force-close every still-open position — including
        # one entered THIS same day — at today's close, reason "end", BEFORE the
        # daily_equity mark below. This keeps daily_equity[-1] in lockstep with
        # final_equity (both realized, both net of commission) instead of the
        # mark showing a gross unrealized mark for a position closed after the
        # loop. A position with no bar for `day` (data gap) is left open — same
        # as any other day where the symbol has no bar.
        if is_last:
            still_open = []
            for pos in open_pos:
                bar = bars_for(pos.symbol).get(day)
                if bar is not None:
                    _close(pos, day, bar.c, "end")
                else:
                    still_open.append(pos)
            open_pos = still_open

        # 4) end-of-day mark: realized equity + open positions marked at today's close
        mark = equity
        for pos in open_pos:
            bar = bars_for(pos.symbol).get(day)
            if bar is not None:
                mark += (bar.c - pos.entry) * pos.shares
        daily_equity.append({"date": day, "equity": round(mark, 2)})

    bt_trades = [
        Trade(symbol=t.symbol, day=t.day, entry_t=t.entry_tod, entry=t.entry, stop=0.0,
              target=0.0, shares=t.shares, exit_t=t.exit_tod, exit=t.exit, pnl=t.pnl,
              r_multiple=t.r_multiple, exit_reason=t.exit_reason)
        for t in trades
    ]
    metrics = asdict(compute_metrics(bt_trades, curve, risk_cfg.account_equity))

    return InsiderResult(
        days=len(days),
        starting_equity=risk_cfg.account_equity,
        final_equity=round(equity, 2),
        n_signals=n_signals, n_taken=n_taken, n_skipped_capacity=n_skip,
        metrics=metrics, equity_curve=curve, daily_equity=daily_equity,
        monthly=_monthly(trades), trades=[asdict(t) for t in trades],
        config=asdict(cfg),
    )
