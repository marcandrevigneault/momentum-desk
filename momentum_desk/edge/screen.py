"""Univariate edge screening: does conditioning on a variable improve forward
return?

We hold the *entry trigger* fixed (the session breakout) and the *exit policy*
fixed (a standardized stop = recent low, 2R target, time cap), then for every
triggered event record (a) the full feature vector at the entry bar and (b) the
realized forward R. Crucially the discretionary RVOL / anti-chase filters are
NOT applied — they're recorded as features — so we can measure whether they
actually help instead of assuming it.

Per feature we then report a Spearman information coefficient (rank correlation
with forward R) and a decile-lift table (mean forward R in each tenth of the
feature's range). That's the first readable answer to "which variables carry
edge" — e.g. "RVOL decile 10 = +0.4R, decile 1 = -0.2R, IC +0.18".
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..backtest.data import MARKET_OPEN_TOD, DayCandidate, HistoricalProvider, MinuteBar
from .features import FEATURES, FeatureContext


@dataclass
class ScreenConfig:
    session: str = "premarket"            # "premarket" | "intraday" | "regular"
    opening_range_minutes: int = 5        # regular / intraday base window
    premarket_or_minutes: int = 15
    entry_cutoff_tod: int = 570           # premarket: no entries at/after 09:30
    intraday_entry_cutoff_tod: int = 660  # intraday: no entries after 11:00
    stop_buffer_pct: float = 0.3
    target_r: float = 2.0                 # standardized take-profit
    fixed_stop_pct: float = 5.0           # H4: a CONSTANT-% stop so risk doesn't vary with entry extension
    max_hold_minutes: int = 60
    min_price: float = 1.0
    max_price: float = 30.0
    min_gap_pct: float = 10.0             # applied only to premarket/regular gate


@dataclass
class EventRow:
    day: str
    symbol: str
    features: dict[str, float | None]
    fwd_r: float        # standardized R, stop = recent/OR low (risk varies with extension)
    mfe_r: float        # max favourable excursion (R) over the hold
    mae_r: float        # max adverse excursion (R) — negative
    fwd_r_fixed: float = 0.0   # H4: R with a CONSTANT-% stop (risk fixed across entries)
    fwd_ret_pct: float = 0.0   # H4: raw forward % return (denominator-free)


@dataclass
class DecileBin:
    lo: float
    hi: float
    n: int
    mean_fwd_r: float


@dataclass
class FeatureScreen:
    name: str
    kind: str
    desc: str
    n: int              # events with a defined value for this feature
    ic: float           # Spearman IC vs standardized R (recent-low stop)
    lift_spread: float  # top-decile mean R minus bottom-decile mean R
    deciles: list[DecileBin] = field(default_factory=list)
    ic_fixed: float = 0.0   # H4: Spearman IC vs FIXED-% stop R (geometry-controlled — the trustworthy one)
    ic_ret: float = 0.0     # H4: Spearman IC vs raw forward % return (denominator-free)


@dataclass
class ScreenResult:
    session: str
    n_events: int
    baseline_fwd_r: float           # mean forward R across all events (the null)
    win_rate: float                 # fraction with fwd_r > 0
    features: list[FeatureScreen] = field(default_factory=list)


# ---- entry-event finders (trigger only; no discretionary filters) ----------

def _find_event(
    bars: list[MinuteBar], cfg: ScreenConfig
) -> tuple[int, float, float, list[MinuteBar]] | None:
    """Return (entry_idx, entry_price, stop, forward_bars) for the session's
    fixed breakout trigger, or None if it never triggered."""
    if cfg.session == "premarket":
        return _event_premarket(bars, cfg)
    if cfg.session == "intraday":
        return _event_intraday(bars, cfg)
    return _event_regular(bars, cfg)


def _event_premarket(bars, cfg):
    pm = [b for b in bars if b.tod < MARKET_OPEN_TOD]
    if len(pm) <= cfg.premarket_or_minutes + 1:
        return None
    or_bars = pm[: cfg.premarket_or_minutes]
    or_high = max(b.h for b in or_bars)
    or_low = min(b.l for b in or_bars)
    or_end_tod = or_bars[-1].tod
    for i, b in enumerate(bars):
        if b.tod <= or_end_tod:
            continue
        if b.tod >= cfg.entry_cutoff_tod:
            break
        if b.h >= or_high:
            stop = or_low * (1 - cfg.stop_buffer_pct / 100.0)
            deadline = MARKET_OPEN_TOD + cfg.max_hold_minutes
            fwd = [x for x in bars[i + 1 :] if x.tod <= deadline]
            return i, or_high, stop, fwd
    return None


def _event_regular(bars, cfg):
    reg = [b for b in bars if b.tod >= MARKET_OPEN_TOD]
    orn = cfg.opening_range_minutes
    if len(reg) <= orn + 1:
        return None
    opening = reg[:orn]
    or_high = max(b.h for b in opening)
    or_low = min(b.l for b in opening)
    # map back to absolute indices so feature context sees all prior bars
    start_abs = len(bars) - len(reg)
    for j in range(orn, len(reg)):
        if reg[j].h >= or_high:
            i = start_abs + j
            stop = or_low * (1 - cfg.stop_buffer_pct / 100.0)
            fwd = reg[j + 1 : j + 1 + cfg.max_hold_minutes]
            return i, or_high, stop, fwd
    return None


def _event_intraday(bars, cfg):
    reg = [b for b in bars if b.tod >= MARKET_OPEN_TOD]
    base_n = cfg.opening_range_minutes
    if len(reg) <= base_n + 2:
        return None
    start_abs = len(bars) - len(reg)
    hod = max(b.h for b in reg[:base_n])
    for j in range(base_n, len(reg)):
        b = reg[j]
        if b.tod >= cfg.intraday_entry_cutoff_tod:
            break
        if b.h >= hod and hod > 0:
            recent_low = min(x.l for x in reg[max(0, j - base_n) : j + 1])
            stop = recent_low * (1 - cfg.stop_buffer_pct / 100.0)
            deadline = cfg.intraday_entry_cutoff_tod + cfg.max_hold_minutes
            fwd = [x for x in reg[j + 1 :] if x.tod <= deadline]
            return start_abs + j, hod, stop, fwd
        hod = max(hod, b.h)
    return None


def _forward_r(entry: float, stop: float, fwd: list[MinuteBar], target_r: float) -> tuple[float, float, float]:
    """Standardized outcome in R: fixed stop / fixed target / time cap, filled
    pessimistically (stop checked before target on the same bar). No slippage —
    this isolates *entry quality*; fills/slippage belong to the exit-policy lab."""
    risk = entry - stop
    if risk <= 0 or not fwd:
        return 0.0, 0.0, 0.0
    target = entry + target_r * risk
    mfe = max((b.h - entry) for b in fwd) / risk
    mae = min((b.l - entry) for b in fwd) / risk
    for b in fwd:
        if b.l <= stop:
            return (stop - entry) / risk, mfe, mae
        if b.h >= target:
            return (target - entry) / risk, mfe, mae
    return (fwd[-1].c - entry) / risk, mfe, mae


def _forward_fixed(entry: float, fwd: list[MinuteBar], stop_pct: float, target_r: float) -> tuple[float, float]:
    """H4 de-confounded outcome. The standardized `_forward_r` puts the stop at the
    recent low, so a more-extended entry has a WIDER stop → larger risk → a fixed
    2R target is mechanically farther → smaller R. That makes the extension/RVOL
    ICs partly geometry. Here the stop is a CONSTANT % below entry, so risk is the
    same fraction for every entry; we report both R (now comparable across the
    extension deciles) and the raw forward % return (denominator-free)."""
    if entry <= 0 or not fwd:
        return 0.0, 0.0
    risk = entry * stop_pct / 100.0
    stop = entry - risk
    target = entry + target_r * risk
    exit_px = fwd[-1].c
    for b in fwd:
        if b.l <= stop:           # pessimistic: stop before target
            exit_px = stop
            break
        if b.h >= target:
            exit_px = target
            break
    return (exit_px - entry) / risk, 100.0 * (exit_px - entry) / entry


def _passes_gate(c: DayCandidate, cfg: ScreenConfig) -> bool:
    if not (cfg.min_price <= c.day_open <= cfg.max_price):
        return False
    if cfg.session != "intraday" and c.gap_pct < cfg.min_gap_pct:
        return False
    return True


def build_events(provider: HistoricalProvider, cfg: ScreenConfig) -> list[EventRow]:
    rows: list[EventRow] = []
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
            ctx = FeatureContext(cand=cand, bars=bars[: entry_idx + 1],
                                 entry_idx=entry_idx, entry_price=entry_price, session=cfg.session)
            feats = {f.name: f.fn(ctx) for f in FEATURES}
            fwd_r, mfe_r, mae_r = _forward_r(entry_price, stop, fwd, cfg.target_r)
            fixed_r, ret_pct = _forward_fixed(entry_price, fwd, cfg.fixed_stop_pct, cfg.target_r)
            rows.append(EventRow(day=day, symbol=cand.symbol, features=feats,
                                 fwd_r=round(fwd_r, 4), mfe_r=round(mfe_r, 4), mae_r=round(mae_r, 4),
                                 fwd_r_fixed=round(fixed_r, 4), fwd_ret_pct=round(ret_pct, 4)))
    return rows


# ---- statistics (stdlib only) ----------------------------------------------

def _avg_ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based average rank for the tie group
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(a: list[float], b: list[float]) -> float:
    n = len(a)
    if n < 3:
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    return cov / (va * vb) ** 0.5 if va > 0 and vb > 0 else 0.0


def _spearman(x: list[float], y: list[float]) -> float:
    return _pearson(_avg_ranks(x), _avg_ranks(y))


def _deciles(pairs: list[tuple[float, float]], n_bins: int = 10) -> list[DecileBin]:
    """pairs = (feature_value, fwd_r), sorted by value, split into ~equal bins."""
    pairs = sorted(pairs, key=lambda p: p[0])
    n = len(pairs)
    if n < n_bins:
        n_bins = max(1, n // 2)
    out: list[DecileBin] = []
    for b in range(n_bins):
        lo_i = b * n // n_bins
        hi_i = (b + 1) * n // n_bins
        chunk = pairs[lo_i:hi_i]
        if not chunk:
            continue
        out.append(DecileBin(
            lo=round(chunk[0][0], 4), hi=round(chunk[-1][0], 4), n=len(chunk),
            mean_fwd_r=round(sum(p[1] for p in chunk) / len(chunk), 4),
        ))
    return out


def screen_features(rows: list[EventRow], n_bins: int = 10) -> list[FeatureScreen]:
    out: list[FeatureScreen] = []
    for f in FEATURES:
        sel = [r for r in rows if r.features[f.name] is not None]
        if len(sel) < 10:
            continue
        xs = [r.features[f.name] for r in sel]
        deciles = _deciles([(r.features[f.name], r.fwd_r) for r in sel], n_bins)
        spread = (deciles[-1].mean_fwd_r - deciles[0].mean_fwd_r) if len(deciles) >= 2 else 0.0
        out.append(FeatureScreen(
            name=f.name, kind=f.kind, desc=f.desc, n=len(sel),
            ic=round(_spearman(xs, [r.fwd_r for r in sel]), 4),
            ic_fixed=round(_spearman(xs, [r.fwd_r_fixed for r in sel]), 4),
            ic_ret=round(_spearman(xs, [r.fwd_ret_pct for r in sel]), 4),
            lift_spread=round(spread, 4), deciles=deciles,
        ))
    # rank by the GEOMETRY-CONTROLLED IC — the trustworthy signal, not the confounded one
    out.sort(key=lambda s: abs(s.ic_fixed), reverse=True)
    return out


def run_screen(provider: HistoricalProvider, cfg: ScreenConfig, n_bins: int = 10) -> ScreenResult:
    rows = build_events(provider, cfg)
    baseline = round(sum(r.fwd_r for r in rows) / len(rows), 4) if rows else 0.0
    win = round(sum(1 for r in rows if r.fwd_r > 0) / len(rows), 4) if rows else 0.0
    return ScreenResult(
        session=cfg.session, n_events=len(rows), baseline_fwd_r=baseline,
        win_rate=win, features=screen_features(rows, n_bins),
    )
