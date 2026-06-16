"""The exit-policy lab: hold the entry fixed, vary only the exit.

The screener showed which *entries* have edge. But most of a momentum strategy's
edge and variance lives in the *exit* — it's an optimal-stopping problem. So here
we take the exact same triggered entries (same trigger, same initial stop) and
run each one through several exit policies, then compare them head-to-head on the
same trades. Differences are attributable purely to the exit.

Outcomes are in R (per-share P&L / per-share risk), filled pessimistically:
on a bar that touches both the stop and the target, the STOP is assumed first;
trailing stops ratchet on the *prior* bar's high-water mark (no intrabar
lookahead); a modelled slippage hits every exit fill.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..backtest.data import HistoricalProvider, MinuteBar
from .screen import ScreenConfig, _find_event, _passes_gate


@dataclass
class ExitPolicy:
    """A declarative, human-readable exit. `target_r` is a fixed take-profit in R
    (None = let it run); `trail_kind` ratchets a stop up behind the high-water
    mark; `vwap_loss` bails on the first close back below VWAP."""

    name: str
    desc: str
    target_r: float | None = None
    trail_kind: str | None = None   # None | "pct" | "atr" | "structure"
    trail_param: float = 0.0        # pct points | ATR multiple | swing-low lookback (bars)
    vwap_loss: bool = False


# The standard panel. Same entries flow through all of these.
POLICIES: list[ExitPolicy] = [
    ExitPolicy("time_only", "Hold to the time cap; initial stop only (no target).", None),
    ExitPolicy("fixed_2r", "Hard stop + take profit at +2R.", 2.0),
    ExitPolicy("fixed_3r", "Hard stop + take profit at +3R.", 3.0),
    ExitPolicy("pct_trail_10", "Ratchet a stop 10% below the high-water mark.", None, "pct", 10.0),
    ExitPolicy("atr_trail_2", "Trail a stop 2×ATR below the high-water mark.", None, "atr", 2.0),
    ExitPolicy("atr_trail_3", "Trail a stop 3×ATR below the high-water mark.", None, "atr", 3.0),
    ExitPolicy("structure_trail", "Trail to the lowest low of the last 5 bars.", None, "structure", 5.0),
    ExitPolicy("vwap_loss", "Exit on the first close back below session VWAP.", None, None, 0.0, True),
    ExitPolicy("fixed_2r_vwap", "+2R target, but also bail on a close below VWAP.", 2.0, None, 0.0, True),
]


@dataclass
class ExitMetrics:
    policy: str
    desc: str
    n: int = 0
    expectancy_r: float = 0.0     # mean R / trade — the headline
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    median_r: float = 0.0
    best_r: float = 0.0
    worst_r: float = 0.0
    max_dd_r: float = 0.0         # max drawdown of the cumulative-R curve
    avg_hold_bars: float = 0.0
    exit_reasons: dict[str, int] = field(default_factory=dict)


@dataclass
class ExitLabResult:
    session: str
    n_events: int
    policies: list[ExitMetrics] = field(default_factory=list)


def _atr(bars: list[MinuteBar]) -> float:
    if len(bars) < 2:
        return 0.0
    trs = [max(bars[i].h - bars[i].l, abs(bars[i].h - bars[i - 1].c), abs(bars[i].l - bars[i - 1].c))
           for i in range(1, len(bars))]
    return sum(trs) / len(trs) if trs else 0.0


@dataclass
class ExitFill:
    r: float
    reason: str
    bars_held: int
    exit_price: float
    exit_tod: int       # ET minute-of-day of the exit bar


def simulate_exit_detail(
    entry: float, init_stop: float, prior_bars: list[MinuteBar], fwd: list[MinuteBar],
    policy: ExitPolicy, slippage_pct: float,
) -> ExitFill:
    """Full exit fill (price, time, reason, R) for one entry under one policy."""
    risk = entry - init_stop
    if risk <= 0 or not fwd:
        return ExitFill(0.0, "void", 0, entry, fwd[0].tod if fwd else 0)
    slip = slippage_pct / 100.0
    target = entry + policy.target_r * risk if policy.target_r is not None else None
    atr = _atr(prior_bars[-14:]) if policy.trail_kind == "atr" else 0.0
    high_water = entry  # as of the *prior* bar — updated at each bar's close

    def fill(px: float, reason: str, b: MinuteBar, i: int) -> ExitFill:
        return ExitFill((px - entry) / risk, reason, i + 1, px, b.tod)

    for i, b in enumerate(fwd):
        # effective stop for THIS bar uses only info known before it (high_water
        # and recent lows from prior bars) — no intrabar lookahead
        eff_stop = init_stop
        if policy.trail_kind == "pct":
            eff_stop = max(eff_stop, high_water * (1 - policy.trail_param / 100.0))
        elif policy.trail_kind == "atr" and atr > 0:
            eff_stop = max(eff_stop, high_water - policy.trail_param * atr)
        elif policy.trail_kind == "structure":
            look = int(policy.trail_param)
            window = fwd[max(0, i - look):i]  # strictly prior forward bars
            if window:
                eff_stop = max(eff_stop, min(x.l for x in window))

        if b.l <= eff_stop:                                   # stop / trail first (pessimistic)
            reason = "stop" if eff_stop == init_stop else "trail"
            return fill(eff_stop * (1 - slip), reason, b, i)
        if target is not None and b.h >= target:
            return fill(target * (1 - slip), "target", b, i)
        if policy.vwap_loss and b.vwap > 0 and b.c < b.vwap:  # momentum-loss exit on the close
            return fill(b.c * (1 - slip), "vwap", b, i)

        high_water = max(high_water, b.h)

    last = fwd[-1]
    return fill(last.c * (1 - slip), "time", last, len(fwd) - 1)   # time cap


def simulate_exit(
    entry: float, init_stop: float, prior_bars: list[MinuteBar], fwd: list[MinuteBar],
    policy: ExitPolicy, slippage_pct: float,
) -> tuple[float, str, int]:
    """Return (r_multiple, exit_reason, bars_held) for one entry under one policy."""
    f = simulate_exit_detail(entry, init_stop, prior_bars, fwd, policy, slippage_pct)
    return f.r, f.reason, f.bars_held


def simulate_fade_detail(
    entry: float, init_stop: float, prior_bars: list[MinuteBar], fwd: list[MinuteBar],
    policy: ExitPolicy, slippage_pct: float,
) -> ExitFill:
    """SHORT (mean-reversion fade) exit — the mirror of simulate_exit_detail. The
    stop sits ABOVE entry; profit is to the downside; a %/ATR trail ratchets DOWN
    behind the low-water mark; slippage is adverse on a short (you cover higher).
    R = (entry - exit) / risk, risk = init_stop - entry."""
    risk = init_stop - entry
    if risk <= 0 or not fwd:
        return ExitFill(0.0, "void", 0, entry, fwd[0].tod if fwd else 0)
    slip = slippage_pct / 100.0
    target = entry - policy.target_r * risk if policy.target_r is not None else None
    atr = _atr(prior_bars[-14:]) if policy.trail_kind == "atr" else 0.0
    low_water = entry  # best (lowest) price seen, updated at each bar's close

    def fill(px: float, reason: str, b: MinuteBar, i: int) -> ExitFill:
        return ExitFill((entry - px) / risk, reason, i + 1, px, b.tod)

    for i, b in enumerate(fwd):
        eff_stop = init_stop
        if policy.trail_kind == "pct":
            eff_stop = min(eff_stop, low_water * (1 + policy.trail_param / 100.0))
        elif policy.trail_kind == "atr" and atr > 0:
            eff_stop = min(eff_stop, low_water + policy.trail_param * atr)
        elif policy.trail_kind == "structure":
            look = int(policy.trail_param)
            window = fwd[max(0, i - look):i]
            if window:
                eff_stop = min(eff_stop, max(x.h for x in window))

        if b.h >= eff_stop:                                   # stop / trail first (pessimistic), cover higher
            reason = "stop" if eff_stop == init_stop else "trail"
            return fill(eff_stop * (1 + slip), reason, b, i)
        if target is not None and b.l <= target:
            return fill(target * (1 + slip), "target", b, i)
        if policy.vwap_loss and b.vwap > 0 and b.c > b.vwap:  # reversion done — back above VWAP
            return fill(b.c * (1 + slip), "vwap", b, i)

        low_water = min(low_water, b.l)

    last = fwd[-1]
    return fill(last.c * (1 + slip), "time", last, len(fwd) - 1)


def _metrics(policy: ExitPolicy, rs: list[float], holds: list[int], reasons: list[str]) -> ExitMetrics:
    m = ExitMetrics(policy=policy.name, desc=policy.desc, n=len(rs))
    if not rs:
        return m
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x <= 0]
    gp, gl = sum(wins), -sum(losses)
    m.expectancy_r = round(sum(rs) / len(rs), 4)
    m.win_rate = round(len(wins) / len(rs), 4)
    m.profit_factor = round(gp / gl, 3) if gl > 0 else float("inf")
    m.avg_win_r = round(gp / len(wins), 4) if wins else 0.0
    m.avg_loss_r = round(-gl / len(losses), 4) if losses else 0.0
    m.median_r = round(sorted(rs)[len(rs) // 2], 4)
    m.best_r, m.worst_r = round(max(rs), 4), round(min(rs), 4)
    m.avg_hold_bars = round(sum(holds) / len(holds), 1)
    m.exit_reasons = dict(Counter(reasons))
    # drawdown of the cumulative-R equity curve
    cum = peak = dd = 0.0
    for x in rs:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    m.max_dd_r = round(dd, 3)
    return m


@dataclass
class _Event:
    entry: float
    init_stop: float
    prior: list[MinuteBar]
    fwd: list[MinuteBar]


def _build_events(provider: HistoricalProvider, cfg: ScreenConfig) -> list[_Event]:
    events: list[_Event] = []
    for day in provider.trading_days():
        for cand in provider.candidates(day):
            if not _passes_gate(cand, cfg):
                continue
            bars = provider.minutes(cand.symbol, day)
            if not bars:
                continue
            ev = _find_event(bars, cfg)
            if ev is None:
                continue
            entry_idx, entry_price, stop, fwd = ev
            if entry_price - stop <= 0 or not fwd:
                continue
            events.append(_Event(entry=entry_price, init_stop=stop,
                                 prior=bars[: entry_idx + 1], fwd=fwd))
    return events


def run_exit_lab(
    provider: HistoricalProvider, cfg: ScreenConfig, slippage_pct: float = 0.3,
    policies: list[ExitPolicy] | None = None,
) -> ExitLabResult:
    policies = policies or POLICIES
    events = _build_events(provider, cfg)
    out = ExitLabResult(session=cfg.session, n_events=len(events))
    for p in policies:
        rs, holds, reasons = [], [], []
        for e in events:
            r, reason, held = simulate_exit(e.entry, e.init_stop, e.prior, e.fwd, p, slippage_pct)
            rs.append(r)
            holds.append(held)
            reasons.append(reason)
        out.policies.append(_metrics(p, rs, holds, reasons))
    out.policies.sort(key=lambda m: m.expectancy_r, reverse=True)
    return out
