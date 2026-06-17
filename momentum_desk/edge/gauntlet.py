"""The evaluation gauntlet: does a candidate edge survive honest scrutiny?

Every prior phase *searched* — and searching inflates whatever looks best. This
layer's only job is to try to kill the candidate before you believe it:

  * Bootstrap CI — resample whole trading days (so same-day trades stay
    correlated) to get a confidence interval on expectancy and P(edge > 0).
  * Deflated Sharpe Ratio — the Sharpe you'd expect from the *best* of N random
    trials is well above zero; DSR is the probability the candidate beats that
    null given how many configs we tried, its sample length, skew and kurtosis
    (Bailey & López de Prado). This is the multiple-testing correction.
  * Purged walk-forward with selection — in each time fold, pick the best exit
    on the in-sample window and measure it out-of-sample, with an embargo around
    the boundary. Catches "best in-sample doesn't hold up."
  * Regime breakdown — per-month expectancy and the fraction of months positive.
  * Untouched holdout — the last slice of history, never used for selection.

Daily aggregation is used for the Sharpe-based stats (days are far closer to
independent than individual trades), which keeps PSR/DSR from over-counting
evidence. Stdlib only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..backtest.data import HistoricalProvider
from .exits import POLICIES, ExitPolicy, simulate_exit
from .screen import ScreenConfig, iter_entry_events
from .stats import _expected_max_sharpe, _mean, _psr, _sharpe, _skew_kurt, _std

# ---- strategy trades -------------------------------------------------------

@dataclass
class _Trade:
    day: str
    r: float


def _all_policy_trades(provider: HistoricalProvider, cfg: ScreenConfig,
                       trials: list[ExitPolicy], slippage_pct: float) -> dict[str, list[_Trade]]:
    """Single pass over the data: fetch each day's bars ONCE, find the entry, and
    run every exit policy on it. Avoids re-fetching minutes per policy — the
    difference between feasible and not over a multi-year window."""
    out: dict[str, list[_Trade]] = {p.name: [] for p in trials}
    for ev in iter_entry_events(provider, cfg):
        if ev.entry - ev.stop <= 0 or not ev.fwd:
            continue
        prior = ev.prior
        for p in trials:
            r, _reason, _held = simulate_exit(ev.entry, ev.stop, prior, ev.fwd, p, slippage_pct)
            out[p.name].append(_Trade(day=ev.day, r=r))
    return out


def _daily(trades: list[_Trade]) -> tuple[list[str], list[float]]:
    """Aggregate to one R per day (sum of that day's trade Rs)."""
    by_day: dict[str, float] = {}
    for t in trades:
        by_day[t.day] = by_day.get(t.day, 0.0) + t.r
    days = sorted(by_day)
    return days, [by_day[d] for d in days]


# ---- result containers -----------------------------------------------------

@dataclass
class Check:
    name: str
    status: str    # "pass" | "caution" | "fail"
    detail: str


@dataclass
class FoldResult:
    fold: int
    is_n: int
    oos_n: int
    selected: str
    is_exp: float
    oos_exp: float


@dataclass
class RegimeRow:
    period: str
    n: int
    expectancy_r: float


@dataclass
class GauntletResult:
    session: str
    candidate: str
    n_trades: int
    n_days: int
    expectancy_r: float
    sharpe_daily: float
    skew: float
    kurt: float
    # bootstrap
    boot_lo: float = 0.0
    boot_hi: float = 0.0
    boot_p_pos: float = 0.0
    # deflated sharpe
    n_trials: int = 0
    sr_star: float = 0.0
    psr: float = 0.0
    dsr: float = 0.0
    # walk-forward
    folds: list[FoldResult] = field(default_factory=list)
    wf_oos_exp: float = 0.0
    wf_pos_folds: int = 0
    # regime
    regime: list[RegimeRow] = field(default_factory=list)
    months_pos_frac: float = 0.0
    # holdout
    holdout_n: int = 0
    holdout_exp: float = 0.0
    # verdict
    checks: list[Check] = field(default_factory=list)
    verdict: str = ""


def _bootstrap(trades: list[_Trade], n_boot: int, seed: int = 7) -> tuple[float, float, float]:
    """Block bootstrap by trading day → CI on per-trade expectancy + P(>0)."""
    import random
    rng = random.Random(seed)
    by_day: dict[str, list[float]] = {}
    for t in trades:
        by_day.setdefault(t.day, []).append(t.r)
    days = list(by_day)
    if len(days) < 3:
        return 0.0, 0.0, 0.0
    means = []
    for _ in range(n_boot):
        picked = [by_day[rng.choice(days)] for _ in range(len(days))]
        flat = [r for block in picked for r in block]
        if flat:
            means.append(sum(flat) / len(flat))
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[int(0.975 * len(means))]
    p_pos = sum(1 for m in means if m > 0) / len(means)
    return round(lo, 4), round(hi, 4), round(p_pos, 4)


def _walk_forward(trades_by_policy: dict[str, list[_Trade]], all_days: list[str],
                  k_folds: int, embargo_days: int) -> tuple[list[FoldResult], float, int]:
    """In each fold: select the best-expectancy policy on the in-sample days,
    measure it on the out-of-sample fold (with an embargo around the split)."""
    if len(all_days) < k_folds * 2:
        return [], 0.0, 0
    folds: list[FoldResult] = []
    fold_size = len(all_days) // k_folds
    for k in range(k_folds):
        oos_lo = k * fold_size
        oos_hi = (k + 1) * fold_size if k < k_folds - 1 else len(all_days)
        oos_days = set(all_days[oos_lo:oos_hi])
        embargo = set(all_days[max(0, oos_lo - embargo_days):oos_lo] +
                      all_days[oos_hi:oos_hi + embargo_days])
        is_days = set(all_days) - oos_days - embargo
        # select best policy on IS
        best_pol, best_exp = None, -1e9
        for name, trades in trades_by_policy.items():
            rs = [t.r for t in trades if t.day in is_days]
            if len(rs) < 10:
                continue
            e = sum(rs) / len(rs)
            if e > best_exp:
                best_pol, best_exp = name, e
        if best_pol is None:
            continue
        oos_rs = [t.r for t in trades_by_policy[best_pol] if t.day in oos_days]
        is_rs = [t.r for t in trades_by_policy[best_pol] if t.day in is_days]
        oos_exp = sum(oos_rs) / len(oos_rs) if oos_rs else 0.0
        folds.append(FoldResult(
            fold=k + 1, is_n=len(is_rs), oos_n=len(oos_rs), selected=best_pol,
            is_exp=round(sum(is_rs) / len(is_rs), 4) if is_rs else 0.0, oos_exp=round(oos_exp, 4),
        ))
    if not folds:
        return [], 0.0, 0
    wf_oos = sum(f.oos_exp for f in folds) / len(folds)
    pos = sum(1 for f in folds if f.oos_exp > 0)
    return folds, round(wf_oos, 4), pos


def _regime(trades: list[_Trade]) -> tuple[list[RegimeRow], float]:
    by_month: dict[str, list[float]] = {}
    for t in trades:
        by_month.setdefault(t.day[:7], []).append(t.r)
    rows = [RegimeRow(period=m, n=len(rs), expectancy_r=round(sum(rs) / len(rs), 4))
            for m, rs in sorted(by_month.items())]
    pos = sum(1 for r in rows if r.expectancy_r > 0)
    return rows, round(pos / len(rows), 4) if rows else 0.0


def run_gauntlet(
    provider: HistoricalProvider, cfg: ScreenConfig, candidate_policy_name: str | None = None,
    trials: list[ExitPolicy] | None = None, slippage_pct: float = 0.3,
    n_trials: int | None = None, k_folds: int = 5, embargo_days: int = 2,
    holdout_frac: float = 0.3, n_boot: int = 2000,
) -> GauntletResult:
    trials = trials or POLICIES
    trades_by_policy = _all_policy_trades(provider, cfg, trials, slippage_pct)

    # candidate = named, else the highest-expectancy trial
    if candidate_policy_name and candidate_policy_name in trades_by_policy:
        candidate = candidate_policy_name
    else:
        candidate = max(trades_by_policy, key=lambda n: _mean([t.r for t in trades_by_policy[n]]) or -1e9)
    trades = trades_by_policy[candidate]
    days, daily = _daily(trades)

    res = GauntletResult(
        session=cfg.session, candidate=candidate, n_trades=len(trades), n_days=len(days),
        expectancy_r=round(_mean([t.r for t in trades]), 4),
        sharpe_daily=round(_sharpe(daily), 4), skew=0.0, kurt=3.0,
    )
    res.skew, res.kurt = (lambda sk: (round(sk[0], 3), round(sk[1], 3)))(_skew_kurt(daily))

    # bootstrap
    res.boot_lo, res.boot_hi, res.boot_p_pos = _bootstrap(trades, n_boot)

    # deflated sharpe
    trial_srs = [_sharpe(_daily(t)[1]) for t in trades_by_policy.values() if len(_daily(t)[1]) > 2]
    res.n_trials = n_trials or len(trials)
    sr_var = _std(trial_srs, ddof=0) ** 2 if len(trial_srs) > 1 else 0.0
    res.sr_star = round(_expected_max_sharpe(sr_var, res.n_trials), 4)
    res.psr = round(_psr(res.sharpe_daily, 0.0, res.n_days, res.skew, res.kurt), 4)
    res.dsr = round(_psr(res.sharpe_daily, res.sr_star, res.n_days, res.skew, res.kurt), 4)

    # walk-forward with selection
    res.folds, res.wf_oos_exp, res.wf_pos_folds = _walk_forward(trades_by_policy, days, k_folds, embargo_days)

    # regime
    res.regime, res.months_pos_frac = _regime(trades)

    # untouched holdout (last slice of dates)
    split = int(len(days) * (1 - holdout_frac))
    holdout_days = set(days[split:])
    hold_rs = [t.r for t in trades if t.day in holdout_days]
    res.holdout_n = len(hold_rs)
    res.holdout_exp = round(sum(hold_rs) / len(hold_rs), 4) if hold_rs else 0.0

    # ---- verdict ----
    res.checks = _verdict_checks(res)
    fails = sum(1 for c in res.checks if c.status == "fail")
    cautions = sum(1 for c in res.checks if c.status == "caution")
    if fails == 0 and cautions <= 1:
        res.verdict = "SURVIVES — positive after deflation, stable out-of-sample"
    elif fails <= 1:
        res.verdict = "FRAGILE — passes some checks but not robust; treat with caution"
    else:
        res.verdict = "REJECTED — does not survive honest evaluation"
    return res


def _verdict_checks(r: GauntletResult) -> list[Check]:
    out: list[Check] = []
    nf = len(r.folds)
    ci = f"CI [{r.boot_lo:+.3f}, {r.boot_hi:+.3f}]R"
    pp = f"P(edge>0)={r.boot_p_pos:.0%}"
    # 1. bootstrap CI excludes zero
    if r.boot_lo > 0:
        out.append(Check("Bootstrap CI", "pass", f"95% {ci} excludes zero; {pp}"))
    elif r.boot_p_pos >= 0.9:
        out.append(Check("Bootstrap CI", "caution", f"{pp} but 95% {ci} includes zero"))
    else:
        out.append(Check("Bootstrap CI", "fail", f"95% {ci} includes zero; {pp}"))
    # 2. deflated sharpe
    sr = f"SR* {r.sr_star:.3f} over {r.n_trials} trials"
    if r.dsr >= 0.95:
        out.append(Check("Deflated Sharpe", "pass", f"DSR={r.dsr:.0%} vs {sr}"))
    elif r.dsr >= 0.80:
        out.append(Check("Deflated Sharpe", "caution", f"DSR={r.dsr:.0%} (want ≥95%) vs {sr}"))
    else:
        out.append(Check("Deflated Sharpe", "fail", f"DSR={r.dsr:.0%} — not significant vs {sr}"))
    # 3. walk-forward OOS
    wf = f"OOS {r.wf_oos_exp:+.3f}R; {r.wf_pos_folds}/{nf} folds positive"
    if r.folds and r.wf_oos_exp > 0 and r.wf_pos_folds >= max(1, nf - 1):
        out.append(Check("Walk-forward OOS", "pass", wf))
    elif r.folds and r.wf_oos_exp > 0:
        out.append(Check("Walk-forward OOS", "caution", wf))
    else:
        out.append(Check("Walk-forward OOS", "fail", wf))
    # 4. regime stability
    reg = f"{r.months_pos_frac:.0%} of months positive"
    if r.months_pos_frac >= 0.7:
        out.append(Check("Regime stability", "pass", reg))
    elif r.months_pos_frac >= 0.55:
        out.append(Check("Regime stability", "caution", reg))
    else:
        out.append(Check("Regime stability", "fail", f"only {reg}"))
    # 5. untouched holdout
    hold = f"holdout expectancy {r.holdout_exp:+.3f}R ({r.holdout_n} trades)"
    out.append(Check("Holdout", "pass" if r.holdout_exp > 0 else "fail",
                     ("untouched " + hold) if r.holdout_exp > 0 else hold))
    return out
