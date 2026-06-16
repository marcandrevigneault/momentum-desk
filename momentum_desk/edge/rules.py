"""Composable entry rules with AND/OR logic, paired with an exit policy.

A RuleSet is a set of feature conditions (e.g. ext_vwap_pct < 8, rvol < 10)
combined with AND or OR, plus an exit policy. We take the session's breakout
events (from the optimizer's one-pass event build), keep those whose features
satisfy the boolean rule, exit on the policy, and report the usual metrics — so
you can compare "low-extension AND rvol-capped" vs "... OR ..." vs single rules.

Features available per event: ext_vwap_pct, rvol, move_from_open_pct (the ones
the edge screen flagged as relevant).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..backtest.data import HistoricalProvider
from .exits import POLICIES, ExitPolicy, simulate_exit
from .gauntlet import _sharpe
from .optimize import EvalEvent, build_eval_events
from .screen import ScreenConfig

_OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


@dataclass
class Condition:
    feature: str          # "ext_vwap_pct" | "rvol" | "move_from_open_pct"
    op: str               # "<" | "<=" | ">" | ">="
    value: float

    def test(self, e: EvalEvent) -> bool:
        v = getattr(e, self.feature, None)
        return v is not None and _OPS[self.op](v, self.value)

    def label(self) -> str:
        return f"{self.feature}{self.op}{self.value:g}"


@dataclass
class RuleSet:
    name: str
    conditions: list[Condition]
    combine: str = "AND"          # "AND" | "OR"
    exit_policy: str = "pct_trail_10"

    def matches(self, e: EvalEvent) -> bool:
        if not self.conditions:
            return True
        tests = [c.test(e) for c in self.conditions]
        return all(tests) if self.combine == "AND" else any(tests)

    def rule_label(self) -> str:
        if not self.conditions:
            return "all entries"
        return f" {self.combine} ".join(c.label() for c in self.conditions)


@dataclass
class RuleResult:
    name: str
    rule: str
    exit_policy: str
    n: int
    expectancy_r: float
    win_rate: float
    profit_factor: float
    daily_sharpe: float


def _policy(name: str) -> ExitPolicy:
    return next((p for p in POLICIES if p.name == name), POLICIES[1])


def run_ruleset(events: list[EvalEvent], rs: RuleSet, slippage_pct: float = 0.3) -> RuleResult:
    policy = _policy(rs.exit_policy)
    rs_events = [e for e in events if rs.matches(e)]
    rs_list, by_day = [], {}
    for e in rs_events:
        r, _reason, _held = simulate_exit(e.entry, e.init_stop, e.prior, e.fwd, policy, slippage_pct)
        rs_list.append(r)
        by_day[e.day] = by_day.get(e.day, 0.0) + r
    if len(rs_list) < 10:
        return RuleResult(rs.name, rs.rule_label(), rs.exit_policy, len(rs_list), 0.0, 0.0, 0.0, 0.0)
    wins = [x for x in rs_list if x > 0]
    losses = [x for x in rs_list if x <= 0]
    gp, gl = sum(wins), -sum(losses)
    daily = [by_day[d] for d in sorted(by_day)]
    return RuleResult(
        name=rs.name, rule=rs.rule_label(), exit_policy=rs.exit_policy, n=len(rs_list),
        expectancy_r=round(sum(rs_list) / len(rs_list), 4),
        win_rate=round(len(wins) / len(rs_list), 4),
        profit_factor=round(gp / gl, 3) if gl > 0 else float("inf"),
        daily_sharpe=round(_sharpe(daily), 4),
    )


# Demonstration presets — same entries, different AND/OR rules + exits.
def presets() -> list[RuleSet]:
    low_ext = Condition("ext_vwap_pct", "<", 8.0)
    rvol_cap = Condition("rvol", "<", 10.0)
    big_move = Condition("move_from_open_pct", ">=", 5.0)
    return [
        RuleSet("baseline (all)", [], "AND", "pct_trail_10"),
        RuleSet("low-ext only", [low_ext], "AND", "fixed_3r"),
        RuleSet("rvol-cap only", [rvol_cap], "AND", "fixed_3r"),
        RuleSet("low-ext AND rvol-cap", [low_ext, rvol_cap], "AND", "fixed_3r"),
        RuleSet("low-ext OR rvol-cap", [low_ext, rvol_cap], "OR", "fixed_3r"),
        RuleSet("rvol-cap AND move≥5", [rvol_cap, big_move], "AND", "pct_trail_10"),
    ]


def run_presets(provider: HistoricalProvider, cfg: ScreenConfig, slippage_pct: float = 0.3) -> list[RuleResult]:
    events = build_eval_events(provider, cfg, slippage_pct)   # one pass over the data
    out = [run_ruleset(events, rs, slippage_pct) for rs in presets()]
    out.sort(key=lambda r: r.daily_sharpe, reverse=True)
    return out


@dataclass
class RulesSnapshot:
    session: str
    n_events: int
    results: list[dict] = field(default_factory=list)
