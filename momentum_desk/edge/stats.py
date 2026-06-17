"""Shared statistics for the edge/evaluation layer — stdlib only.

These were originally defined inside ``gauntlet.py``; optimize, rules and tuner
all reached into the gauntlet module just to borrow them. They are generic
(normal CDF/inverse, moments, Sharpe, probabilistic/deflated-Sharpe inputs), so
they live here and the gauntlet, optimizer, rules and tuner all import from one
place. Names keep their leading underscore for a verbatim, behaviour-preserving
move (gauntlet re-exports them, so existing importers/tests are unaffected).
"""
from __future__ import annotations

import math

_EULER = 0.5772156649015329


# ---- normal CDF / inverse CDF ----------------------------------------------

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse normal CDF — Acklam's rational approximation."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


# ---- moments ---------------------------------------------------------------

def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float], ddof: int = 1) -> float:
    n = len(xs)
    if n - ddof <= 0:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - ddof))


def _skew_kurt(xs: list[float]) -> tuple[float, float]:
    n = len(xs)
    s = _std(xs, ddof=0)
    if n < 3 or s == 0:
        return 0.0, 3.0
    m = _mean(xs)
    g3 = sum((x - m) ** 3 for x in xs) / n / s ** 3
    g4 = sum((x - m) ** 4 for x in xs) / n / s ** 4   # non-excess (normal = 3)
    return g3, g4


def _sharpe(xs: list[float]) -> float:
    s = _std(xs)
    return _mean(xs) / s if s > 0 else 0.0


# ---- probabilistic / deflated Sharpe ---------------------------------------

def _psr(sr: float, sr_star: float, n: int, skew: float, kurt: float) -> float:
    """Probability the true Sharpe exceeds sr_star, given the sample."""
    if n < 2:
        return 0.0
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if denom <= 0:
        return 0.0
    return _norm_cdf((sr - sr_star) * math.sqrt(n - 1) / math.sqrt(denom))


def _expected_max_sharpe(trial_sr_var: float, n_trials: int) -> float:
    """E[max Sharpe] across N independent zero-skill trials — the null a real
    edge must beat (Bailey & López de Prado)."""
    if n_trials < 2 or trial_sr_var <= 0:
        return 0.0
    sd = math.sqrt(trial_sr_var)
    z1 = _norm_ppf(1.0 - 1.0 / n_trials)
    z2 = _norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    return sd * ((1.0 - _EULER) * z1 + _EULER * z2)
