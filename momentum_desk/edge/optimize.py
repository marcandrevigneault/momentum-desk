"""Parameter optimizer — search hundreds of strategy configurations, honestly.

Two ideas make this correct rather than a curve-fitting machine:

  1. Build the entry events (and their forward bars) ONCE, then evaluate every
     configuration in memory. The expensive part is the data; config evaluation
     is pure CPU, so hundreds–thousands of configs are cheap (and trivially
     parallelisable across processes).
  2. DEFLATE the winner. Picking the best of N configs inflates its Sharpe — the
     more you search, the better the best looks by luck alone. So after the
     search we compute the Deflated Sharpe Ratio of the top config against the
     null of having tried N trials (Bailey & López de Prado), using the spread of
     Sharpes across the whole search. A config that doesn't clear its own
     deflated bar is overfit, full stop.

The optimiser tunes the entry FILTERS that the Phase-1 screen suggested matter
(cap extension, cap — don't require — RVOL, minimum move) and the exit policy.
"""
from __future__ import annotations

import itertools
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

from ..backtest.data import HistoricalProvider, MinuteBar
from .exits import ExitPolicy, simulate_exit
from .screen import ScreenConfig, _find_event, _passes_gate
from .stats import _expected_max_sharpe, _psr, _sharpe, _skew_kurt, _std


@dataclass
class EvalEvent:
    """A triggered entry with everything needed to score any config in memory."""
    day: str
    entry: float
    init_stop: float
    ext_vwap_pct: float
    rvol: float
    move_from_open_pct: float
    prior: list[MinuteBar]
    fwd: list[MinuteBar]


@dataclass
class ParamConfig:
    max_ext_pct: float | None    # cap entry extension above VWAP (None = no cap)
    rvol_min: float              # require RVOL ≥ this
    rvol_max: float | None       # cap RVOL ≤ this (None = no cap)
    min_move_pct: float          # require move-from-open ≥ this
    exit_policy: ExitPolicy

    def label(self) -> str:
        return (f"ext≤{self.max_ext_pct if self.max_ext_pct is not None else '∞'}·"
                f"rvol[{self.rvol_min},{self.rvol_max if self.rvol_max is not None else '∞'}]·"
                f"mv≥{self.min_move_pct}·{self.exit_policy.name}")


@dataclass
class ConfigResult:
    label: str
    n: int
    expectancy_r: float
    daily_sharpe: float
    win_rate: float
    profit_factor: float


@dataclass
class OptimizeResult:
    n_configs: int
    n_events: int
    ranked: list[ConfigResult] = field(default_factory=list)
    # honest deflation of the winner
    best_label: str = ""
    best_sharpe: float = 0.0
    sr_star: float = 0.0
    deflated_sharpe: float = 0.0
    note: str = ""


def build_eval_events(provider: HistoricalProvider, cfg: ScreenConfig,
                      slippage_pct: float) -> list[EvalEvent]:
    events: list[EvalEvent] = []
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
            entry_idx, entry, stop, fwd = ev
            if entry - stop <= 0 or not fwd:
                continue
            eb = bars[entry_idx]
            ext = 100.0 * (entry - eb.vwap) / eb.vwap if eb.vwap > 0 else 0.0
            rvol = eb.cum_volume / cand.avg_volume_20d if cand.avg_volume_20d > 0 else 0.0
            move = 100.0 * (entry - cand.day_open) / cand.day_open if cand.day_open > 0 else 0.0
            events.append(EvalEvent(day=day, entry=entry, init_stop=stop, ext_vwap_pct=ext,
                                    rvol=rvol, move_from_open_pct=move,
                                    prior=bars[: entry_idx + 1], fwd=fwd))
    return events


def _daily_returns(rs_by_day: dict[str, float]) -> list[float]:
    return [rs_by_day[d] for d in sorted(rs_by_day)]


def eval_config(events: list[EvalEvent], cfg: ParamConfig, slippage_pct: float) -> ConfigResult:
    rs: list[float] = []
    by_day: dict[str, float] = {}
    for e in events:
        if cfg.max_ext_pct is not None and e.ext_vwap_pct > cfg.max_ext_pct:
            continue
        if e.rvol < cfg.rvol_min:
            continue
        if cfg.rvol_max is not None and e.rvol > cfg.rvol_max:
            continue
        if e.move_from_open_pct < cfg.min_move_pct:
            continue
        r, _reason, _held = simulate_exit(e.entry, e.init_stop, e.prior, e.fwd, cfg.exit_policy, slippage_pct)
        rs.append(r)
        by_day[e.day] = by_day.get(e.day, 0.0) + r
    if len(rs) < 20:
        return ConfigResult(cfg.label(), len(rs), 0.0, 0.0, 0.0, 0.0)
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x <= 0]
    gp, gl = sum(wins), -sum(losses)
    return ConfigResult(
        label=cfg.label(), n=len(rs),
        expectancy_r=round(sum(rs) / len(rs), 4),
        daily_sharpe=round(_sharpe(_daily_returns(by_day)), 4),
        win_rate=round(len(wins) / len(rs), 4),
        profit_factor=round(gp / gl, 3) if gl > 0 else float("inf"),
    )


# ---- the search space ------------------------------------------------------

def default_grid() -> list[ParamConfig]:
    ext_caps = [None, 15.0, 10.0, 6.0]
    rvol_mins = [0.0, 2.0, 3.0]
    rvol_maxs = [None, 20.0, 10.0]
    min_moves = [0.0, 3.0, 5.0]
    exits = [
        ExitPolicy("pct_trail_6", "", None, "pct", 6.0),
        ExitPolicy("pct_trail_10", "", None, "pct", 10.0),
        ExitPolicy("pct_trail_15", "", None, "pct", 15.0),
        ExitPolicy("atr_trail_2", "", None, "atr", 2.0),
        ExitPolicy("atr_trail_3", "", None, "atr", 3.0),
        ExitPolicy("fixed_2r", "", 2.0),
        ExitPolicy("fixed_3r", "", 3.0),
    ]
    grid = []
    for ec, rmn, rmx, mv, ex in itertools.product(ext_caps, rvol_mins, rvol_maxs, min_moves, exits):
        grid.append(ParamConfig(max_ext_pct=ec, rvol_min=rmn, rvol_max=rmx, min_move_pct=mv, exit_policy=ex))
    return grid


# process-pool plumbing: events live as a module global in each worker so they
# aren't re-pickled per task
_WORKER_EVENTS: list[EvalEvent] = []
_WORKER_SLIP = 0.3


def _worker_init(events: list[EvalEvent], slippage_pct: float) -> None:
    global _WORKER_EVENTS, _WORKER_SLIP
    _WORKER_EVENTS = events
    _WORKER_SLIP = slippage_pct


def _worker_eval(cfg: ParamConfig) -> ConfigResult:
    return eval_config(_WORKER_EVENTS, cfg, _WORKER_SLIP)


def optimize(provider: HistoricalProvider, cfg: ScreenConfig, grid: list[ParamConfig] | None = None,
             slippage_pct: float = 0.3, workers: int = 1, min_trades: int = 50) -> OptimizeResult:
    grid = grid or default_grid()
    events = build_eval_events(provider, cfg, slippage_pct)

    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers, initializer=_worker_init,
                                 initargs=(events, slippage_pct)) as ex:
            results = list(ex.map(_worker_eval, grid, chunksize=8))
    else:
        results = [eval_config(events, c, slippage_pct) for c in grid]

    valid = [r for r in results if r.n >= min_trades]
    valid.sort(key=lambda r: r.daily_sharpe, reverse=True)

    out = OptimizeResult(n_configs=len(grid), n_events=len(events), ranked=valid)
    if valid:
        # DEFLATE: the best of N configs is inflated. Bar = E[max Sharpe] over the
        # trials, using the spread of Sharpes actually observed in the search.
        sharpes = [r.daily_sharpe for r in valid]
        sr_var = _std(sharpes, ddof=0) ** 2 if len(sharpes) > 1 else 0.0
        best = valid[0]
        out.best_label = best.label
        out.best_sharpe = best.daily_sharpe
        out.sr_star = round(_expected_max_sharpe(sr_var, len(grid)), 4)
        # daily-Sharpe DSR needs the candidate's daily-return moments; recompute
        skew, kurt, n_days = _winner_daily_moments(events, _config_by_label(grid, best.label), slippage_pct)
        out.deflated_sharpe = round(_psr(best.daily_sharpe, out.sr_star, n_days, skew, kurt), 4)
        if out.deflated_sharpe < 0.95:
            out.note = (f"WINNER LIKELY OVERFIT: deflated Sharpe {out.deflated_sharpe:.0%} < 95% after "
                        f"searching {len(grid)} configs — its edge does not clear the multiple-testing bar.")
        else:
            out.note = (f"Winner clears its deflated bar (DSR {out.deflated_sharpe:.0%}) even after "
                        f"{len(grid)} trials — not merely the luckiest of the search.")
    return out


def _config_by_label(grid: list[ParamConfig], label: str) -> ParamConfig:
    return next(c for c in grid if c.label() == label)


def _winner_daily_moments(events: list[EvalEvent], cfg: ParamConfig,
                          slippage_pct: float) -> tuple[float, float, int]:
    by_day: dict[str, float] = {}
    for e in events:
        if cfg.max_ext_pct is not None and e.ext_vwap_pct > cfg.max_ext_pct:
            continue
        if e.rvol < cfg.rvol_min or (cfg.rvol_max is not None and e.rvol > cfg.rvol_max):
            continue
        if e.move_from_open_pct < cfg.min_move_pct:
            continue
        r, _re, _h = simulate_exit(e.entry, e.init_stop, e.prior, e.fwd, cfg.exit_policy, slippage_pct)
        by_day[e.day] = by_day.get(e.day, 0.0) + r
    daily = _daily_returns(by_day)
    skew, kurt = _skew_kurt(daily)
    return skew, kurt, len(daily)
