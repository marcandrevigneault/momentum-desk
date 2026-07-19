"""Multi-day daily-bar portfolio simulator: exit-priority ladder (hard stop >
trailing stop > time stop > end-of-window), gap-fill semantics, sizing/
compounding, commissions, and capacity/gross counters. A hand-built FakeDaily
provider gives explicit OHLC bars so every fill, share count, and pnl is
arithmetic-checkable here rather than trusted from the implementation."""
from __future__ import annotations

from dataclasses import dataclass, field

from momentum_desk.edge.result import AccountRun
from momentum_desk.insider.models import InsiderConfig, InsiderEvent
from momentum_desk.insider.prices import DailyBar
from momentum_desk.insider.simulate import InsiderResult, run_insider
from momentum_desk.risk import RiskConfig


@dataclass
class FakeDaily:
    name: str = "fake"
    days: list[str] = field(default_factory=list)
    bars: dict[str, list[DailyBar]] = field(default_factory=dict)

    def trading_days(self) -> list[str]:
        return list(self.days)

    def daily(self, symbol: str) -> list[DailyBar]:
        return list(self.bars.get(symbol, []))


def bar(day, o, h, l, c, v=100_000):
    return DailyBar(day=day, o=o, h=h, l=l, c=c, v=v)


def event(symbol, trigger_day):
    return InsiderEvent(symbol=symbol, trigger_day=trigger_day, total_value=100_000.0,
                         n_insiders=1, top_role="ceo", conviction=0.5)


D = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]


def test_entry_next_open_with_slippage():
    # stop/trail set far away so the position simply rides to the last day's
    # close ("end") — this test is about the ENTRY fill, not the exit.
    days = D[:3]
    provider = FakeDaily(days=days, bars={
        "AAA": [
            bar(days[0], o=100, h=101, l=99, c=100.5),
            bar(days[1], o=101, h=102, l=100, c=101),
            bar(days[2], o=102, h=103, l=101, c=102.5),
        ],
    })
    cfg = InsiderConfig(stop_pct=50.0, trail_pct=50.0, hold_days=5)
    result = run_insider([event("AAA", days[0])], provider, cfg, RiskConfig())

    assert len(result.trades) == 1
    t = result.trades[0]
    assert t["entry"] == 100.3   # 100 * 1.003
    assert t["exit"] == 102.5
    assert t["exit_reason"] == "end"
    assert t["shares"] == 4
    assert t["pnl"] == 6.8
    assert t["r_multiple"] == 0.027


def test_hard_stop_intraday_fill():
    days = D[:2]
    provider = FakeDaily(days=days, bars={
        "AAA": [
            bar(days[0], o=100, h=101, l=99, c=100),
            bar(days[1], o=99, h=100, l=85, c=95),   # low pierces stop=90
        ],
    })
    cfg = InsiderConfig(stop_pct=10.0, trail_pct=90.0, hold_days=99)
    result = run_insider([event("AAA", days[0])], provider, cfg, RiskConfig(),
                          slippage_pct=0.0)

    t = result.trades[0]
    assert t["exit_reason"] == "stop"
    assert t["exit"] == 90.0     # fills at the stop, not the low
    assert t["shares"] == 25
    assert t["pnl"] == -252.0
    assert t["r_multiple"] == -1.008


def test_hard_stop_gap_through_fills_open():
    days = D[:2]
    provider = FakeDaily(days=days, bars={
        "AAA": [
            bar(days[0], o=100, h=101, l=99, c=100),
            bar(days[1], o=85, h=86, l=84, c=84),    # opens BELOW stop=90
        ],
    })
    cfg = InsiderConfig(stop_pct=10.0, trail_pct=90.0, hold_days=99)
    result = run_insider([event("AAA", days[0])], provider, cfg, RiskConfig(),
                          slippage_pct=0.0)

    t = result.trades[0]
    assert t["exit_reason"] == "stop"
    assert t["exit"] == 85.0     # gap-through: fills at the open, not the stop price
    assert t["shares"] == 25
    assert t["pnl"] == -377.0
    assert t["r_multiple"] == -1.508


def test_trailing_stop_from_high_close():
    days = D[:2]
    provider = FakeDaily(days=days, bars={
        "AAA": [
            bar(days[0], o=100, h=111, l=99, c=110),   # entry-day close seeds the trail high
            bar(days[1], o=105, h=106, l=95, c=104),   # trail = 110*0.9 = 99; low pierces it
        ],
    })
    cfg = InsiderConfig(stop_pct=50.0, trail_pct=10.0, hold_days=99)
    result = run_insider([event("AAA", days[0])], provider, cfg, RiskConfig(),
                          slippage_pct=0.0)

    t = result.trades[0]
    assert t["exit_reason"] == "trail"
    assert t["exit"] == 99.0     # 110 * (1 - 0.10), NOT off entry(100) or today's close(104)
    assert t["shares"] == 5
    assert t["pnl"] == -7.0
    assert t["r_multiple"] == -0.028


def test_time_stop_after_hold_days():
    days = D   # 4 days so day2 (the time-stop day) is not also the last day
    provider = FakeDaily(days=days, bars={
        "AAA": [
            bar(days[0], o=100, h=101, l=99, c=100),
            bar(days[1], o=101, h=102, l=99, c=101),
            bar(days[2], o=102, h=103, l=100, c=103),   # hold_days=2 reached here
            bar(days[3], o=110, h=111, l=109, c=110),
        ],
    })
    cfg = InsiderConfig(stop_pct=90.0, trail_pct=90.0, hold_days=2)
    result = run_insider([event("AAA", days[0])], provider, cfg, RiskConfig(),
                          slippage_pct=0.0)

    t = result.trades[0]
    assert t["exit_reason"] == "time"
    assert t["exit_day"] == days[2]
    assert t["exit"] == 103.0
    assert t["hold_days"] == 2
    assert t["shares"] == 2
    assert t["pnl"] == 4.0
    assert t["r_multiple"] == 0.016


def test_time_stop_fires_on_next_bar_after_gap_on_exact_hold_day():
    """Review finding: `held == cfg.hold_days` never fires if the symbol has
    no bar on the EXACT hold day (halt/gap in real data) — the position then
    rides all the way to "end" instead of exiting on a time stop. `>=` fixes
    it: the position exits at the NEXT available bar's close, reason "time"."""
    days = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
    provider = FakeDaily(days=days, bars={
        "AAA": [
            bar(days[0], o=100, h=101, l=99, c=100),
            bar(days[1], o=101, h=102, l=99, c=101),
            # days[2] (the exact hold_days=2 day): NO BAR — data gap/halt.
            bar(days[3], o=104, h=105, l=103, c=104),
            bar(days[4], o=110, h=111, l=109, c=110),
        ],
    })
    cfg = InsiderConfig(stop_pct=90.0, trail_pct=90.0, hold_days=2)
    result = run_insider([event("AAA", days[0])], provider, cfg, RiskConfig(),
                          slippage_pct=0.0)

    assert len(result.trades) == 1
    t = result.trades[0]
    assert t["exit_reason"] == "time"
    assert t["exit_day"] == days[3]
    assert t["entry"] == 100.0
    assert t["exit"] == 104.0
    assert t["shares"] == 2
    assert t["pnl"] == 6.0
    assert t["r_multiple"] == 0.024
    assert t["hold_days"] == 3


def test_end_of_window_close():
    days = D[:2]
    provider = FakeDaily(days=days, bars={
        "AAA": [
            bar(days[0], o=100, h=101, l=99, c=100),
            bar(days[1], o=101, h=102, l=95, c=105),   # last day, nothing else triggers
        ],
    })
    cfg = InsiderConfig(stop_pct=90.0, trail_pct=90.0, hold_days=100)
    result = run_insider([event("AAA", days[0])], provider, cfg, RiskConfig(),
                          slippage_pct=0.0)

    t = result.trades[0]
    assert t["exit_reason"] == "end"
    assert t["exit_day"] == days[1]
    assert t["exit"] == 105.0
    assert t["hold_days"] == 1
    assert t["shares"] == 2
    assert t["pnl"] == 8.0
    assert t["r_multiple"] == 0.032


def test_max_concurrent_skips_and_counts():
    days = D[:2]
    provider = FakeDaily(days=days, bars={
        "AAA": [
            bar(days[0], o=100, h=101, l=99, c=100),
            bar(days[1], o=101, h=102, l=95, c=102),
        ],
        "BBB": [
            bar(days[0], o=50, h=51, l=49, c=50),
            bar(days[1], o=51, h=52, l=48, c=51),
        ],
    })
    result = run_insider(
        [event("AAA", days[0]), event("BBB", days[0])],
        provider, InsiderConfig(), RiskConfig(),
        max_concurrent=1, slippage_pct=0.0,
    )

    assert result.n_signals == 2
    assert result.n_taken == 1
    assert result.n_skipped_capacity == 1
    assert len(result.trades) == 1
    assert result.trades[0]["symbol"] == "AAA"
    # arithmetic check on the one trade that got in (default cfg: stop_pct=20)
    assert result.trades[0]["shares"] == 12
    assert result.trades[0]["pnl"] == 22.0


def test_compound_vs_fixed_sizing():
    days = D
    bars = {
        "AAA": [
            bar(days[0], o=100, h=101, l=99, c=100),
            bar(days[1], o=200, h=1005, l=195, c=1000),   # time-stop exit, huge gain
            bar(days[2], o=1000, h=1001, l=999, c=1000),
            bar(days[3], o=1000, h=1001, l=999, c=1000),
        ],
        "BBB": [
            bar(days[0], o=50, h=51, l=49, c=50),
            bar(days[1], o=50, h=51, l=49, c=50),
            bar(days[2], o=100, h=101, l=99, c=100),
            bar(days[3], o=105, h=106, l=100, c=110),
        ],
    }
    cfg = InsiderConfig(stop_pct=10.0, trail_pct=90.0, hold_days=1)
    evs = [event("AAA", days[0]), event("BBB", days[2])]

    fixed = run_insider(evs, FakeDaily(days=days, bars=bars), cfg,
                         RiskConfig(account_equity=25_000.0, max_risk_per_trade_pct=1.0, compound=False),
                         slippage_pct=0.0)
    compound = run_insider(evs, FakeDaily(days=days, bars=bars), cfg,
                            RiskConfig(account_equity=25_000.0, max_risk_per_trade_pct=1.0, compound=True),
                            slippage_pct=0.0)

    aaa_fixed = next(t for t in fixed.trades if t["symbol"] == "AAA")
    aaa_compound = next(t for t in compound.trades if t["symbol"] == "AAA")
    assert aaa_fixed["pnl"] == 22498.0
    assert aaa_compound["pnl"] == 22498.0   # first trade: identical, no realized pnl yet

    bbb_fixed = next(t for t in fixed.trades if t["symbol"] == "BBB")
    bbb_compound = next(t for t in compound.trades if t["symbol"] == "BBB")
    assert bbb_fixed["entry"] == 100.0
    assert bbb_compound["entry"] == 100.0
    # fixed sizes off the $25k starting balance; compound off equity after AAA's
    # +22498 realized gain (equity=47498) -> materially bigger size
    assert bbb_fixed["shares"] == 25
    assert bbb_compound["shares"] == 47


def test_daily_equity_marks_open_positions():
    days = D[:3]
    provider = FakeDaily(days=days, bars={
        "AAA": [
            bar(days[0], o=100, h=101, l=99, c=105),
            bar(days[1], o=106, h=110, l=104, c=108),
            bar(days[2], o=109, h=112, l=107, c=111),   # last day -> "end"
        ],
    })
    cfg = InsiderConfig(stop_pct=90.0, trail_pct=90.0, hold_days=100)
    result = run_insider([event("AAA", days[0])], provider, cfg, RiskConfig(),
                          slippage_pct=0.0)

    assert result.trades[0]["shares"] == 2
    assert result.daily_equity == [
        {"date": days[0], "equity": 25010.0},   # 25000 + (105-100)*2 unrealized
        {"date": days[1], "equity": 25016.0},   # 25000 + (108-100)*2 unrealized
        {"date": days[2], "equity": 25020.0},   # realized: 25000 + pnl(20)
    ]
    assert result.final_equity == 25020.0


def test_final_day_entry_closes_within_day_loop():
    # Event triggers on the ONLY (hence also the last) trading day: the
    # position is entered and force-closed same-day, net of commission,
    # BEFORE that day's daily_equity mark is appended — so daily_equity[-1]
    # must equal final_equity exactly (regression for the post-loop-sweep
    # mismatch, where the mark was taken gross before the sweep closed it).
    days = D[:1]
    provider = FakeDaily(days=days, bars={
        "AAA": [bar(days[0], o=100, h=105, l=99, c=102)],
    })
    cfg = InsiderConfig(stop_pct=90.0, trail_pct=90.0, hold_days=100)
    result = run_insider([event("AAA", days[0])], provider, cfg, RiskConfig(),
                          slippage_pct=0.0)

    assert len(result.trades) == 1
    t = result.trades[0]
    assert t["exit_reason"] == "end"
    assert t["exit_day"] == days[0]
    assert t["entry"] == 100.0
    assert t["exit"] == 102.0
    assert t["shares"] == 2
    assert t["pnl"] == 2.0   # gross (102-100)*2=4.0 minus $2.0 round-turn commission
    assert t["hold_days"] == 0

    assert result.final_equity == 25002.0
    assert result.daily_equity == [{"date": days[0], "equity": 25002.0}]
    assert result.daily_equity[-1]["equity"] == round(result.final_equity, 2)


def test_result_is_account_run_with_metrics():
    days = D[:2]
    provider = FakeDaily(days=days, bars={
        "AAA": [
            bar(days[0], o=100, h=101, l=99, c=100),
            bar(days[1], o=101, h=102, l=95, c=105),
        ],
    })
    cfg = InsiderConfig(stop_pct=90.0, trail_pct=90.0, hold_days=100)
    result = run_insider([event("AAA", days[0])], provider, cfg, RiskConfig(),
                          slippage_pct=0.0)

    assert isinstance(result, AccountRun)
    assert isinstance(result, InsiderResult)
    assert result.metrics["trades"] == 1
    assert result.metrics["total_pnl"] == 8.0
    assert result.config["stop_pct"] == 90.0
