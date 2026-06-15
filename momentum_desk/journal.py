"""Trade journal — append-only log of everything the desk saw and did.

A self-aware losing trader improves by *reviewing decisions*, not by taking
more of them. So every signal, every decision (taken or skipped — and why), and
every fill is written as one JSON line to a journal file. Later you replay it
and ask the only question that matters: which of my rules actually made money,
and where did I override them?

Pure stdlib. Files land in `journal/` (gitignored).

    python -m momentum_desk.journal journal/2025-06-15.jsonl     # review a session
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


class Journal:
    """Append-only JSONL writer/reader. One file per session is the usual unit."""

    def __init__(self, path: str | Path, clock=time.time) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock

    # ---- writing ----
    def record(self, kind: str, **fields: Any) -> dict:
        """Append one event. `kind` is e.g. 'signal' | 'decision' | 'fill'."""
        entry = {"ts": round(self._clock(), 3), "kind": kind, **_jsonable(fields)}
        with self.path.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")
        return entry

    def log_signal(self, signal: Any) -> dict:
        return self.record("signal", **_jsonable(_to_dict(signal)))

    def log_decision(self, symbol: str, action: str, reason: str, **extra: Any) -> dict:
        """action: 'taken' | 'skipped'. reason is free text (or a Flag value)."""
        return self.record("decision", symbol=symbol, action=action, reason=reason, **_jsonable(extra))

    def log_fill(self, trade: Any) -> dict:
        return self.record("fill", **_jsonable(_to_dict(trade)))

    # ---- reading ----
    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open() as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out


def summarize(entries: list[dict]) -> dict:
    """Roll a journal up into the numbers you'd review after a session."""
    signals = [e for e in entries if e.get("kind") == "signal"]
    decisions = [e for e in entries if e.get("kind") == "decision"]
    fills = [e for e in entries if e.get("kind") == "fill"]
    taken = [d for d in decisions if d.get("action") == "taken"]
    skipped = [d for d in decisions if d.get("action") == "skipped"]

    pnls = [f.get("pnl", 0.0) for f in fills]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total = round(sum(pnls), 2)
    gross_win = round(sum(wins), 2)
    gross_loss = round(-sum(losses), 2)

    return {
        "signals": len(signals),
        "decisions": len(decisions),
        "taken": len(taken),
        "skipped": len(skipped),
        "fills": len(fills),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(100.0 * len(wins) / len(fills), 1) if fills else 0.0,
        "total_pnl": total,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else (float("inf") if gross_win else 0.0),
        "expectancy": round(total / len(fills), 2) if fills else 0.0,
    }


def _to_dict(obj: Any) -> dict:
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    raise TypeError(f"cannot journal {type(obj).__name__}; pass a dataclass or dict")


def _jsonable(value: Any) -> Any:
    """Recursively coerce enums/dataclasses to JSON-safe primitives."""
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "value") and hasattr(value, "name"):  # Enum / StrEnum
        return value.value
    return value


def _main() -> None:
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m momentum_desk.journal <file.jsonl>")
    j = Journal(sys.argv[1])
    s = summarize(j.entries())
    print(f"Journal · {j.path}")
    print("─" * 48)
    print(f"  signals seen      {s['signals']}")
    print(f"  decisions         {s['decisions']}  (taken {s['taken']} · skipped {s['skipped']})")
    print(f"  fills             {s['fills']}  (wins {s['wins']} · losses {s['losses']})")
    if s["fills"]:
        print(f"  win rate          {s['win_rate']:.1f}%")
        print(f"  profit factor     {s['profit_factor']}")
        print(f"  expectancy        ${s['expectancy']:,.2f}/trade")
        print(f"  total P&L         ${s['total_pnl']:,.2f}")


if __name__ == "__main__":
    _main()
