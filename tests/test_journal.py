"""Tests for the trade journal: round-trip, enum/dataclass coercion, and that
the summary matches the underlying backtest."""
from __future__ import annotations

from momentum_desk.backtest import Backtester, SyntheticHistory
from momentum_desk.journal import Journal, summarize
from momentum_desk.models import Flag, Signal


def test_record_round_trip(tmp_path):
    j = Journal(tmp_path / "s.jsonl", clock=lambda: 1000.0)
    j.record("decision", symbol="ABCD", action="skipped", reason="extended")
    j.record("decision", symbol="WXYZ", action="taken", reason="clean breakout")
    entries = j.entries()
    assert [e["kind"] for e in entries] == ["decision", "decision"]
    assert entries[0] == {"ts": 1000.0, "kind": "decision", "symbol": "ABCD",
                          "action": "skipped", "reason": "extended"}


def test_signal_with_enum_flags_is_jsonable(tmp_path):
    j = Journal(tmp_path / "s.jsonl")
    sig = Signal(symbol="ABCD", score=9.1, last=3.2, gap_pct=40, relative_volume=8,
                 extension_above_vwap_pct=12, float_millions=3.0, has_news=True,
                 news_headline="x", flags=[Flag.EXTENDED])
    j.log_signal(sig)
    e = j.entries()[0]
    assert e["kind"] == "signal"
    assert e["flags"] == ["extended_above_vwap"]   # StrEnum coerced to its value


def test_summary_matches_backtest(tmp_path):
    res = Backtester(SyntheticHistory(days=40)).run()
    j = Journal(tmp_path / "bt.jsonl")
    for t in res.trades:
        j.log_fill(t)
    s = summarize(j.entries())
    assert s["fills"] == res.metrics.trades
    assert s["wins"] == res.metrics.wins
    assert abs(s["total_pnl"] - res.metrics.total_pnl) < 1.0
    assert s["win_rate"] == res.metrics.win_rate


def test_empty_journal_is_safe(tmp_path):
    s = summarize(Journal(tmp_path / "none.jsonl").entries())
    assert s["fills"] == 0 and s["total_pnl"] == 0.0 and s["win_rate"] == 0.0
