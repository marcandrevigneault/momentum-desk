"""SQLite store for the Strategy Lab — strategies, their runs, and the active pick.

Replaces the sprawl of parallel JSON snapshot files (sim_*.json, combos_*.json,
optimize.json, …) with one queryable place that knows every strategy and every
run and how they rank. Stdlib sqlite3 only; the DB lives on the mounted data
volume (data/lab.db by default), so it survives restarts like the snapshots did.

Schema:
  strategies(name PK, config JSON, created)
  runs(id PK, strategy, kind, window, data_source, generated, metrics JSON,
       final_equity, result JSON)
  meta(key PK, value)            -- holds the single 'active' strategy name
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .result import AccountRun
from .strategy import Strategy

# Metrics a run can be ranked by on the leaderboard (allowlisted to keep the
# json_extract path injection-free).
RANKABLE = frozenset({
    "expectancy_r", "profit_factor", "win_rate", "total_pnl", "return_pct",
    "max_drawdown_pct", "trades",
})
_DEFAULT_RANK = "expectancy_r"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class LabStore:
    def __init__(self, path: str | Path = "data/lab.db") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS strategies (
                name    TEXT PRIMARY KEY,
                config  TEXT NOT NULL,
                created TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy     TEXT NOT NULL,
                kind         TEXT NOT NULL,
                window       TEXT NOT NULL,
                data_source  TEXT NOT NULL,
                generated    TEXT NOT NULL,
                metrics      TEXT NOT NULL,
                final_equity REAL NOT NULL,
                result       TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---- strategies -------------------------------------------------------

    def save_strategy(self, strategy: Strategy) -> None:
        self._conn.execute(
            "INSERT INTO strategies(name, config, created) VALUES(?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET config=excluded.config",
            (strategy.name, json.dumps(strategy.to_dict()), _now()),
        )
        self._conn.commit()

    def get_strategy(self, name: str) -> Strategy | None:
        row = self._conn.execute("SELECT config FROM strategies WHERE name=?", (name,)).fetchone()
        return Strategy.from_dict(json.loads(row["config"])) if row else None

    def list_strategies(self) -> list[Strategy]:
        rows = self._conn.execute("SELECT config FROM strategies ORDER BY name").fetchall()
        return [Strategy.from_dict(json.loads(r["config"])) for r in rows]

    def delete_strategy(self, name: str) -> None:
        self._conn.execute("DELETE FROM strategies WHERE name=?", (name,))
        self._conn.commit()

    def rename_strategy(self, old: str, new: str) -> bool:
        """Rename a strategy and re-point its history (runs + active). Returns
        False if `old` is missing or `new` already exists."""
        if old == new:
            return True
        cur = self._conn.execute("SELECT config FROM strategies WHERE name=?", (old,)).fetchone()
        if cur is None or self._conn.execute("SELECT 1 FROM strategies WHERE name=?", (new,)).fetchone():
            return False
        # the config carries its own name; rewrite it too
        cfg = json.loads(cur["config"])
        cfg["name"] = new
        self._conn.execute("UPDATE strategies SET name=?, config=? WHERE name=?",
                           (new, json.dumps(cfg), old))
        self._conn.execute("UPDATE runs SET strategy=? WHERE strategy=?", (new, old))
        if self.get_active() == old:
            self.set_active(new)
        self._conn.commit()
        return True

    # ---- runs -------------------------------------------------------------

    def save_run(self, strategy: Strategy, window: str, data_source: str, result: AccountRun) -> int:
        cur = self._conn.execute(
            "INSERT INTO runs(strategy, kind, window, data_source, generated, metrics, final_equity, result) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (strategy.name, strategy.kind, window, data_source, _now(),
             json.dumps(result.metrics), float(result.final_equity), json.dumps(asdict(result))),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def leaderboard(self, *, rank_by: str = _DEFAULT_RANK, limit: int = 100) -> list[dict]:
        """Runs ranked by a metric (descending; drawdown ranks ascending — lower
        is better). Returns lightweight rows for the table, not the full result."""
        col = rank_by if rank_by in RANKABLE else _DEFAULT_RANK
        ascending = col == "max_drawdown_pct"
        rows = self._conn.execute(
            f"SELECT id, strategy, kind, window, data_source, generated, metrics, final_equity "
            f"FROM runs ORDER BY CAST(json_extract(metrics, '$.{col}') AS REAL) "
            f"{'ASC' if ascending else 'DESC'} LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["metrics"] = json.loads(d["metrics"])
            out.append(d)
        return out

    def get_run(self, run_id: int) -> dict | None:
        row = self._conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["metrics"] = json.loads(d["metrics"])
        d["result"] = json.loads(d["result"])
        return d

    # ---- active selection -------------------------------------------------

    def set_active(self, name: str) -> None:
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES('active', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (name,),
        )
        self._conn.commit()

    def get_active(self) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key='active'").fetchone()
        return row["value"] if row else None
