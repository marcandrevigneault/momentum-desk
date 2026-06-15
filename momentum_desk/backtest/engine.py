"""The backtester: opening-range-breakout on scanner candidates, sized by the
same RiskEngine the live desk uses, filled pessimistically.

Honesty rules that keep results from lying:
  * No lookahead. Each decision uses only bars up to that minute. Gap is known
    at the open; RVOL and VWAP-extension are checked at the entry bar from
    session-to-date totals.
  * Adverse slippage on every fill (buy up, sell down) — bigger is realistic on
    thin low-float names; this is the #1 reason live trails backtest.
  * Commissions both sides.
  * Same-bar stop-and-target ⇒ assume the STOP filled first (pessimistic).
  * The live guards apply: anti-chase VWAP filter, position/liquidity caps, and
    the daily-loss circuit breaker that halts new entries for the day.

The strategy here is deliberately simple and explicit. It is NOT advice — it's a
testbed so you can measure expectancy before risking money.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import Snapshot
from ..risk import RiskEngine, RiskConfig
from ..scanner import ScanConfig
from .data import BacktestResult, DayCandidate, HistoricalProvider, Metrics, MinuteBar, Trade


@dataclass
class BacktestConfig:
    opening_range_minutes: int = 5     # define the breakout level from the first N bars
    target_r: float = 2.0              # take profit at this multiple of risk
    max_hold_minutes: int = 60         # time-stop (first hour by default)
    stop_buffer_pct: float = 0.3       # stop placed this % below the opening-range low
    use_anti_chase: bool = True        # skip entries already extended above VWAP
    slippage_pct: float = 0.1          # adverse, each side (10 bps; thin names worse)
    commission_per_share: float = 0.005
    commission_min: float = 1.0


class Backtester:
    def __init__(
        self,
        provider: HistoricalProvider,
        scan: ScanConfig | None = None,
        risk: RiskConfig | None = None,
        bt: BacktestConfig | None = None,
    ) -> None:
        self.provider = provider
        self.scan = scan or ScanConfig()
        self.risk_cfg = risk or RiskConfig()
        self.bt = bt or BacktestConfig()

    # ---------- public ----------
    def run(self) -> BacktestResult:
        risk = RiskEngine(self.risk_cfg)
        equity = self.risk_cfg.account_equity
        curve = [equity]
        trades: list[Trade] = []
        skipped = 0
        days = self.provider.trading_days()

        for day in days:
            risk.realized_pnl_today = 0.0  # fresh circuit breaker each session
            cands = sorted(self.provider.candidates(day), key=lambda c: c.gap_pct, reverse=True)
            for cand in cands:
                if risk.daily_loss_limit_hit:
                    break  # done trading this day
                if not self._passes_open_gate(cand):
                    continue
                trade = self._simulate(cand, risk)
                if trade is None:
                    skipped += 1
                    continue
                risk.record_fill(trade.pnl)
                equity += trade.pnl
                curve.append(equity)
                trades.append(trade)

        metrics = self._metrics(trades, curve, self.risk_cfg.account_equity)
        return BacktestResult(
            metrics=metrics, trades=trades, equity_curve=curve,
            starting_equity=self.risk_cfg.account_equity, days=len(days),
            skipped_no_entry=skipped,
        )

    # ---------- candidate gate (known at the open) ----------
    def _passes_open_gate(self, c: DayCandidate) -> bool:
        s = self.scan
        if not (s.min_price <= c.day_open <= s.max_price):
            return False
        if c.gap_pct < s.min_gap_pct:
            return False
        if s.require_news and not c.has_news:
            return False
        if c.float_shares is not None and c.float_shares / 1e6 > s.max_float_millions:
            return False
        return True

    # ---------- one trade ----------
    def _simulate(self, c: DayCandidate, risk: RiskEngine) -> Trade | None:
        bars = self.provider.minutes(c.symbol, c.day)
        orn = self.bt.opening_range_minutes
        if len(bars) <= orn + 1:
            return None

        opening = bars[:orn]
        or_high = max(b.h for b in opening)
        or_low = min(b.l for b in opening)
        stop = or_low * (1 - self.bt.stop_buffer_pct / 100.0)

        # entry: first bar after the opening range whose high breaks the OR high
        entry_idx = None
        for i in range(orn, len(bars)):
            if bars[i].h >= or_high:
                entry_idx = i
                break
        if entry_idx is None:
            return None  # never broke out

        eb = bars[entry_idx]
        entry = or_high * (1 + self.bt.slippage_pct / 100.0)  # buy slips up
        if stop >= entry:
            return None

        # entry-time filters using session-to-date info (no lookahead)
        rvol = eb.cum_volume / c.avg_volume_20d if c.avg_volume_20d > 0 else 0.0
        if rvol < self.scan.min_relative_volume:
            return None
        ext = 100.0 * (entry - eb.vwap) / eb.vwap if eb.vwap > 0 else 0.0
        if self.bt.use_anti_chase and ext > self.scan.max_extension_above_vwap_pct:
            return None  # too extended — would be chasing

        snap = Snapshot(
            symbol=c.symbol, last=entry, prev_close=c.prev_close, day_open=c.day_open,
            vwap=eb.vwap, cum_volume=eb.cum_volume, avg_volume_20d=c.avg_volume_20d,
            float_shares=c.float_shares,
        )
        plan = risk.plan(snap, entry=entry, stop=stop)
        if not plan.ok or plan.shares <= 0:
            return None

        target = entry + self.bt.target_r * (entry - stop)
        slip = self.bt.slippage_pct / 100.0
        exit_idx, exit_px, reason = entry_idx, eb.c, "time"
        last_idx = min(entry_idx + self.bt.max_hold_minutes, len(bars) - 1)

        for i in range(entry_idx + 1, last_idx + 1):
            b = bars[i]
            if b.l <= stop:                       # pessimistic: stop checked first
                exit_idx, exit_px, reason = i, stop * (1 - slip), "stop"
                break
            if b.h >= target:
                exit_idx, exit_px, reason = i, target * (1 - slip), "target"
                break
        else:
            exit_idx, exit_px, reason = last_idx, bars[last_idx].c * (1 - slip), "time"

        gross = (exit_px - entry) * plan.shares
        commission = 2.0 * max(self.bt.commission_min, plan.shares * self.bt.commission_per_share)
        pnl = gross - commission
        r = pnl / plan.risk_dollars if plan.risk_dollars > 0 else 0.0
        return Trade(
            symbol=c.symbol, day=c.day, entry_t=eb.t, entry=round(entry, 4), stop=round(stop, 4),
            target=round(target, 4), shares=plan.shares, exit_t=bars[exit_idx].t,
            exit=round(exit_px, 4), pnl=round(pnl, 2), r_multiple=round(r, 2), exit_reason=reason,
        )

    # ---------- metrics ----------
    @staticmethod
    def _metrics(trades: list[Trade], curve: list[float], start: float) -> Metrics:
        m = Metrics()
        m.trades = len(trades)
        if not trades:
            return m
        wins = [t.pnl for t in trades if t.pnl > 0]
        losses = [t.pnl for t in trades if t.pnl <= 0]
        m.wins, m.losses = len(wins), len(losses)
        m.win_rate = round(100.0 * m.wins / m.trades, 1)
        m.gross_profit = round(sum(wins), 2)
        m.gross_loss = round(-sum(losses), 2)
        m.profit_factor = round(m.gross_profit / m.gross_loss, 2) if m.gross_loss > 0 else float("inf")
        m.avg_win = round(m.gross_profit / m.wins, 2) if m.wins else 0.0
        m.avg_loss = round(-m.gross_loss / m.losses, 2) if m.losses else 0.0
        m.total_pnl = round(sum(t.pnl for t in trades), 2)
        m.expectancy = round(m.total_pnl / m.trades, 2)
        m.expectancy_r = round(sum(t.r_multiple for t in trades) / m.trades, 3)
        m.return_pct = round(100.0 * m.total_pnl / start, 2) if start > 0 else 0.0

        peak = curve[0]
        max_dd = 0.0
        for eq in curve:
            peak = max(peak, eq)
            max_dd = max(max_dd, peak - eq)
        m.max_drawdown = round(max_dd, 2)
        m.max_drawdown_pct = round(100.0 * max_dd / peak, 2) if peak > 0 else 0.0
        return m
