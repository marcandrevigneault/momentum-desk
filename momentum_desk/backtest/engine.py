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
from ..risk import RiskConfig, RiskEngine
from ..scanner import ScanConfig
from .data import MARKET_OPEN_TOD, BacktestResult, DayCandidate, HistoricalProvider, Metrics, Trade


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
    # --- pre-market session (entry 04:00–09:30, held into the open) ---
    session: str = "regular"           # "regular" | "premarket"
    premarket_or_minutes: int = 15     # opening range measured from 04:00
    entry_cutoff_tod: int = 570        # no new entries at/after this ET minute (570 = 09:30)
    premarket_slippage_pct: float = 0.5  # wider — thin pre-market books
    premarket_volume_fraction: float = 0.1  # RVOL baseline: ~10% of daily avg trades pre-market
    # --- "where to stop": force flat at a wall-clock ET time (0 = disabled) ---
    # Momentum runners often fade ~10:00–10:30 ET; capping the hold there is a
    # rule worth measuring (sweep it). 600 = 10:00, 630 = 10:30.
    time_exit_tod: int = 0
    # --- intraday / post-open momentum (session="intraday") ---
    # Catches names that open flat/down then run on volume — entry on a new
    # high-of-day break after the open, with a momentum + RVOL confirmation.
    intraday_min_move_pct: float = 5.0    # min % up from the open at the HOD break
                                          # (kept modest so it doesn't fight the anti-chase guard)
    intraday_entry_cutoff_tod: int = 660  # no new entries after this ET min (660 = 11:00)


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
    def run(self, on_progress=None) -> BacktestResult:
        """on_progress(fraction) is called per day — a good proxy for a real
        run's fetch progress, since each day triggers its data pulls."""
        risk = RiskEngine(self.risk_cfg)
        equity = self.risk_cfg.account_equity
        curve = [equity]
        trades: list[Trade] = []
        skipped = 0
        days = self.provider.trading_days()
        n_days = len(days)

        for di, day in enumerate(days):
            if on_progress is not None:
                on_progress((di + 1) / n_days if n_days else 1.0)
            risk.realized_pnl_today = 0.0  # fresh circuit breaker each session
            cands = sorted(self.provider.candidates(day), key=lambda c: c.gap_pct, reverse=True)
            for cand in cands:
                if risk.daily_loss_limit_hit:
                    break  # done trading this day
                if not self._passes_open_gate(cand):
                    continue
                sim = {"premarket": self._simulate_premarket,
                       "intraday": self._simulate_intraday}.get(self.bt.session, self._simulate)
                trade = sim(cand, risk)
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
        # intraday mode trades names that opened flat/down then ran — so it does
        # NOT require an open gap; the universe + the HOD-break RVOL check select.
        if self.bt.session != "intraday" and c.gap_pct < s.min_gap_pct:
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
            if self.bt.time_exit_tod and b.tod >= self.bt.time_exit_tod:
                exit_idx, exit_px, reason = i, b.c * (1 - slip), "time-cap"
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

    # ---------- one pre-market trade (entry 04:00–09:30, held into the open) ----------
    def _simulate_premarket(self, c: DayCandidate, risk: RiskEngine) -> Trade | None:
        bars = self.provider.minutes(c.symbol, c.day)
        pm = [b for b in bars if b.tod < MARKET_OPEN_TOD]      # pre-market bars only
        orn = self.bt.premarket_or_minutes
        if len(pm) <= orn + 1:
            return None
        or_bars = pm[:orn]
        or_high = max(b.h for b in or_bars)
        or_low = min(b.l for b in or_bars)
        stop = or_low * (1 - self.bt.stop_buffer_pct / 100.0)
        slip = self.bt.premarket_slippage_pct / 100.0
        or_end_tod = or_bars[-1].tod

        # entry: first breakout of the OR high after the opening range, but
        # before the entry cutoff (09:30) — pre-market entries only
        entry_idx = None
        for i, b in enumerate(bars):
            if b.tod <= or_end_tod:
                continue
            if b.tod >= self.bt.entry_cutoff_tod:
                break
            if b.h >= or_high:
                entry_idx = i
                break
        if entry_idx is None:
            return None

        eb = bars[entry_idx]
        entry = or_high * (1 + slip)
        if stop >= entry:
            return None
        # the real gap at entry is pre-market price vs the prior close
        if 100.0 * (entry - c.prev_close) / c.prev_close < self.scan.min_gap_pct:
            return None
        # pre-market RVOL against a pre-market baseline (a fraction of daily avg)
        base = c.avg_volume_20d * self.bt.premarket_volume_fraction
        if base > 0 and eb.cum_volume / base < self.scan.min_relative_volume:
            return None
        ext = 100.0 * (entry - eb.vwap) / eb.vwap if eb.vwap > 0 else 0.0
        if self.bt.use_anti_chase and ext > self.scan.max_extension_above_vwap_pct:
            return None

        snap = Snapshot(
            symbol=c.symbol, last=entry, prev_close=c.prev_close, day_open=c.day_open,
            vwap=eb.vwap, cum_volume=eb.cum_volume, avg_volume_20d=c.avg_volume_20d,
            float_shares=c.float_shares,
        )
        plan = risk.plan(snap, entry=entry, stop=stop)
        if not plan.ok or plan.shares <= 0:
            return None

        target = entry + self.bt.target_r * (entry - stop)
        # hold INTO the open: a pre-market entry is carried through 09:30 and up
        # to max_hold_minutes past the open, managing stop/target throughout
        # (pessimistic same-bar). This is the whole point of session B.
        deadline_tod = MARKET_OPEN_TOD + self.bt.max_hold_minutes
        exit_t, exit_px, reason = eb.t, eb.c, "time"
        for b in bars[entry_idx + 1:]:
            if b.tod > deadline_tod:
                exit_t, exit_px, reason = b.t, b.c * (1 - slip), "time"
                break
            if b.l <= stop:
                exit_t, exit_px, reason = b.t, stop * (1 - slip), "stop"
                break
            if b.h >= target:
                exit_t, exit_px, reason = b.t, target * (1 - slip), "target"
                break
            if self.bt.time_exit_tod and b.tod >= self.bt.time_exit_tod:
                exit_t, exit_px, reason = b.t, b.c * (1 - slip), "time-cap"
                break
            exit_t, exit_px = b.t, b.c * (1 - slip)   # carry a time-exit fallback

        gross = (exit_px - entry) * plan.shares
        commission = 2.0 * max(self.bt.commission_min, plan.shares * self.bt.commission_per_share)
        pnl = gross - commission
        r = pnl / plan.risk_dollars if plan.risk_dollars > 0 else 0.0
        return Trade(
            symbol=c.symbol, day=c.day, entry_t=eb.t, entry=round(entry, 4), stop=round(stop, 4),
            target=round(target, 4), shares=plan.shares, exit_t=exit_t,
            exit=round(exit_px, 4), pnl=round(pnl, 2), r_multiple=round(r, 2), exit_reason=reason,
        )

    # ---------- one intraday / post-open momentum trade ----------
    def _simulate_intraday(self, c: DayCandidate, risk: RiskEngine) -> Trade | None:
        """Enter on a new high-of-day break AFTER the open — catches names that
        opened flat/down and ran on volume (no gap required). Point-in-time:
        only bars up to the breakout decide entry."""
        bars = self.provider.minutes(c.symbol, c.day)
        reg = [b for b in bars if b.tod >= MARKET_OPEN_TOD]   # regular session only
        base_n = self.bt.opening_range_minutes
        if len(reg) <= base_n + 2:
            return None
        open_px = reg[0].o
        slip = self.bt.slippage_pct / 100.0
        if open_px <= 0:
            return None

        hod = max(b.h for b in reg[:base_n])     # high of the opening base
        entry_idx, entry, stop = None, 0.0, 0.0
        for i in range(base_n, len(reg)):
            b = reg[i]
            if b.tod >= self.bt.intraday_entry_cutoff_tod:
                break
            if b.h >= hod and hod > 0:           # new high-of-day → momentum break
                move = 100.0 * (hod - open_px) / open_px
                rvol = b.cum_volume / c.avg_volume_20d if c.avg_volume_20d > 0 else 0.0
                ext = 100.0 * (hod - b.vwap) / b.vwap if b.vwap > 0 else 0.0
                chasing = self.bt.use_anti_chase and ext > self.scan.max_extension_above_vwap_pct
                if move >= self.bt.intraday_min_move_pct and rvol >= self.scan.min_relative_volume and not chasing:
                    recent_low = min(x.l for x in reg[max(0, i - base_n): i + 1])
                    entry = hod * (1 + slip)
                    stop = recent_low * (1 - self.bt.stop_buffer_pct / 100.0)
                    entry_idx = i
                    break
            hod = max(hod, b.h)
        if entry_idx is None or stop <= 0 or stop >= entry:
            return None

        eb = reg[entry_idx]
        snap = Snapshot(
            symbol=c.symbol, last=entry, prev_close=c.prev_close, day_open=c.day_open,
            vwap=eb.vwap, cum_volume=eb.cum_volume, avg_volume_20d=c.avg_volume_20d,
            float_shares=c.float_shares,
        )
        plan = risk.plan(snap, entry=entry, stop=stop)
        if not plan.ok or plan.shares <= 0:
            return None

        target = entry + self.bt.target_r * (entry - stop)
        deadline_tod = self.bt.intraday_entry_cutoff_tod + self.bt.max_hold_minutes
        exit_t, exit_px, reason = eb.t, eb.c, "time"
        for b in reg[entry_idx + 1:]:
            if b.tod > deadline_tod:
                exit_t, exit_px, reason = b.t, b.c * (1 - slip), "time"
                break
            if b.l <= stop:
                exit_t, exit_px, reason = b.t, stop * (1 - slip), "stop"
                break
            if b.h >= target:
                exit_t, exit_px, reason = b.t, target * (1 - slip), "target"
                break
            if self.bt.time_exit_tod and b.tod >= self.bt.time_exit_tod:
                exit_t, exit_px, reason = b.t, b.c * (1 - slip), "time-cap"
                break
            exit_t, exit_px = b.t, b.c * (1 - slip)

        gross = (exit_px - entry) * plan.shares
        commission = 2.0 * max(self.bt.commission_min, plan.shares * self.bt.commission_per_share)
        pnl = gross - commission
        r = pnl / plan.risk_dollars if plan.risk_dollars > 0 else 0.0
        return Trade(
            symbol=c.symbol, day=c.day, entry_t=eb.t, entry=round(entry, 4), stop=round(stop, 4),
            target=round(target, 4), shares=plan.shares, exit_t=exit_t,
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
