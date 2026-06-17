import { useEffect, useState } from "react";
import {
  getLabRun, getLabStrategies, getLeaderboard, LabStrategy, LeaderRow,
  runLabStrategy, setLabActive,
} from "../api";
import BacktesterPage from "./BacktesterPage";
import EdgePage from "./EdgePage";
import ExitLabPage from "./ExitLabPage";
import GauntletPage from "./GauntletPage";
import RulesPage from "./RulesPage";
import TunerPage from "./TunerPage";

// The Strategy Lab — one surface for every strategy: define, run, rank, and pick
// the active one (Leaderboard), with the analysis tools folded in as tabs.
// Consolidates Analyser/Simulation/Combo (-> Leaderboard) and hosts the edge
// pipeline + tuners as sub-views, all under one nav entry.

type Tab = "leaderboard" | "backtester" | "edge" | "exits" | "gauntlet" | "rules" | "tuner";
const TABS: { id: Tab; label: string }[] = [
  { id: "leaderboard", label: "Leaderboard" },
  { id: "backtester", label: "Backtester" },
  { id: "edge", label: "Edge" },
  { id: "exits", label: "Exit lab" },
  { id: "gauntlet", label: "Gauntlet" },
  { id: "rules", label: "Rules" },
  { id: "tuner", label: "Tuner" },
];

const money = (v: number) => (v ?? 0).toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const rColor = (v: number) => (v >= 0 ? "var(--green)" : "var(--red)");

const RANKS: { k: string; label: string }[] = [
  { k: "expectancy_r", label: "Expectancy R" },
  { k: "profit_factor", label: "Profit factor" },
  { k: "return_pct", label: "Return %" },
  { k: "win_rate", label: "Win %" },
  { k: "max_drawdown_pct", label: "Max DD %" },
  { k: "trades", label: "Trades" },
];

function Spark({ curve }: { curve: number[] }) {
  if (!curve || curve.length < 2) return null;
  const w = 520, h = 90, lo = Math.min(...curve), hi = Math.max(...curve);
  const span = hi - lo || 1;
  const pts = curve.map((v, i) => `${(i / (curve.length - 1)) * w},${h - ((v - lo) / span) * h}`).join(" ");
  const up = curve[curve.length - 1] >= curve[0];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: "100%", height: 90 }}>
      <polyline points={pts} fill="none" stroke={up ? "var(--green)" : "var(--red)"} strokeWidth={1.5} />
    </svg>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="rounded-lg px-3 py-2" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
      <div className="text-[10px] uppercase tracking-wide" style={{ color: "var(--muted)" }}>{label}</div>
      <div className="mono text-[15px] font-semibold" style={{ color: color ?? "var(--text)" }}>{value}</div>
    </div>
  );
}

function LeaderboardTab() {
  const [strategies, setStrategies] = useState<LabStrategy[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [board, setBoard] = useState<LeaderRow[]>([]);
  const [rankBy, setRankBy] = useState("expectancy_r");
  const [win, setWin] = useState("1y");
  const [running, setRunning] = useState<string | null>(null);
  const [selected, setSelected] = useState<any | null>(null);

  const reloadBoard = async (rb = rankBy) => setBoard(await getLeaderboard(rb));
  const reloadStrats = async () => {
    const s = await getLabStrategies();
    setStrategies(s.strategies); setActive(s.active);
  };
  useEffect(() => { reloadStrats(); }, []);
  useEffect(() => { reloadBoard(rankBy); }, [rankBy]);

  const run = async (name: string) => {
    setRunning(name);
    try {
      const out = await runLabStrategy(name, win);
      await reloadBoard();
      if (out.run_id) setSelected(await getLabRun(out.run_id));
    } finally { setRunning(null); }
  };
  const pickRow = async (r: LeaderRow) => setSelected(await getLabRun(r.id));
  const makeActive = async (name: string) => { await setLabActive(name); setActive(name); };

  const sel = selected?.result;
  const m = sel?.metrics ?? {};

  return (
    <div className="h-full overflow-auto p-4 flex flex-col gap-4">
      {/* header */}
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="text-[15px] font-bold m-0">Strategy Lab</h2>
        <span className="text-[12px]" style={{ color: "var(--muted)" }}>define · run · rank · activate — every strategy in one place</span>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-[11px]" style={{ color: "var(--muted)" }}>window</span>
          <div className="flex rounded-md overflow-hidden" style={{ border: "1px solid var(--line)" }}>
            {["1y", "5y"].map((w) => (
              <button key={w} onClick={() => setWin(w)} className="mono text-[11px] px-3 py-1"
                style={{ background: win === w ? "var(--panel-2)" : "transparent", color: win === w ? "var(--text)" : "var(--muted)" }}>
                {w}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* strategies — run buttons + active */}
      <div className="flex flex-wrap gap-2">
        {strategies.map((s) => (
          <div key={s.name} className="rounded-lg px-3 py-2 flex items-center gap-3"
            style={{ background: "var(--panel)", border: `1px solid ${active === s.name ? "var(--green)" : "var(--line)"}` }}>
            <button title="set active" onClick={() => makeActive(s.name)} className="text-[14px]"
              style={{ color: active === s.name ? "var(--green)" : "var(--muted)" }}>★</button>
            <div className="flex flex-col">
              <span className="text-[12px] font-semibold">{s.name}</span>
              <span className="mono text-[10px]" style={{ color: "var(--muted)" }}>
                {s.kind}{s.kind === "combo" ? ` · ${s.legs.length} legs` : ` · ${s.session}`} · {s.sizing.mode} {s.sizing.risk_pct}%
              </span>
            </div>
            <button onClick={() => run(s.name)} disabled={running === s.name}
              className="mono text-[11px] px-2 py-1 rounded"
              style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)", opacity: running === s.name ? 0.5 : 1 }}>
              {running === s.name ? "running…" : `run ${win}`}
            </button>
          </div>
        ))}
      </div>

      {/* leaderboard */}
      <div className="rounded-lg" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
        <div className="flex items-center gap-2 px-3 py-2" style={{ borderBottom: "1px solid var(--line)" }}>
          <span className="text-[12px] font-semibold">Leaderboard</span>
          <span className="text-[11px] ml-auto" style={{ color: "var(--muted)" }}>rank by</span>
          <select value={rankBy} onChange={(e) => setRankBy(e.target.value)} className="mono text-[11px] px-2 py-1 rounded"
            style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }}>
            {RANKS.map((r) => <option key={r.k} value={r.k}>{r.label}</option>)}
          </select>
        </div>
        <table className="w-full text-[12px]">
          <thead>
            <tr style={{ color: "var(--muted)" }} className="text-left">
              <th className="px-3 py-1.5 font-medium">#</th>
              <th className="px-3 py-1.5 font-medium">Strategy</th>
              <th className="px-3 py-1.5 font-medium">Win</th>
              <th className="px-3 py-1.5 font-medium mono">expR</th>
              <th className="px-3 py-1.5 font-medium mono">PF</th>
              <th className="px-3 py-1.5 font-medium mono">win%</th>
              <th className="px-3 py-1.5 font-medium mono">maxDD%</th>
              <th className="px-3 py-1.5 font-medium mono">final</th>
            </tr>
          </thead>
          <tbody>
            {board.length === 0 && (
              <tr><td colSpan={8} className="px-3 py-4 text-center" style={{ color: "var(--muted)" }}>
                No runs yet — hit “run” on a strategy above.</td></tr>
            )}
            {board.map((r, i) => (
              <tr key={r.id} onClick={() => pickRow(r)} className="cursor-pointer"
                style={{ borderTop: "1px solid var(--line)", background: selected?.id === r.id ? "var(--panel-2)" : undefined }}>
                <td className="px-3 py-1.5 mono" style={{ color: "var(--muted)" }}>{i + 1}</td>
                <td className="px-3 py-1.5">{r.strategy}{active === r.strategy && <span style={{ color: "var(--green)" }}> ★</span>}</td>
                <td className="px-3 py-1.5 mono">{r.window}</td>
                <td className="px-3 py-1.5 mono" style={{ color: rColor(r.metrics.expectancy_r) }}>{(r.metrics.expectancy_r ?? 0).toFixed(2)}</td>
                <td className="px-3 py-1.5 mono">{(r.metrics.profit_factor ?? 0).toFixed(2)}</td>
                <td className="px-3 py-1.5 mono">{(r.metrics.win_rate ?? 0).toFixed(0)}</td>
                <td className="px-3 py-1.5 mono">{(r.metrics.max_drawdown_pct ?? 0).toFixed(1)}</td>
                <td className="px-3 py-1.5 mono">{money(r.final_equity)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* selected run result */}
      {sel && (
        <div className="rounded-lg p-3 flex flex-col gap-3" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
          <div className="text-[12px] font-semibold">
            {selected.strategy} · {selected.window}
            <span className="mono text-[11px] ml-2" style={{ color: "var(--muted)" }}>{selected.data_source}</span>
          </div>
          <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))" }}>
            <Stat label="Final equity" value={money(sel.final_equity)} />
            <Stat label="Return" value={`${(m.return_pct ?? 0).toFixed(0)}%`} color={rColor(m.return_pct ?? 0)} />
            <Stat label="Expectancy R" value={(m.expectancy_r ?? 0).toFixed(3)} color={rColor(m.expectancy_r ?? 0)} />
            <Stat label="Profit factor" value={(m.profit_factor ?? 0).toFixed(2)} />
            <Stat label="Win rate" value={`${(m.win_rate ?? 0).toFixed(1)}%`} />
            <Stat label="Max DD" value={`${(m.max_drawdown_pct ?? 0).toFixed(1)}%`} color="var(--red)" />
            <Stat label="Trades" value={String(m.trades ?? 0)} />
          </div>
          <Spark curve={sel.equity_curve ?? []} />
        </div>
      )}
    </div>
  );
}

export default function LabPage() {
  const [tab, setTab] = useState<Tab>("leaderboard");
  return (
    <div className="h-full flex flex-col min-h-0">
      {/* tab bar — the analysis tools live here, under one nav entry */}
      <div className="flex items-center gap-1 px-3 h-10 shrink-0 overflow-x-auto"
        style={{ background: "var(--panel)", borderBottom: "1px solid var(--line)" }}>
        {TABS.map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className="mono text-[11px] px-3 py-1.5 rounded-md whitespace-nowrap"
            style={{
              background: tab === t.id ? "var(--panel-2)" : "transparent",
              color: tab === t.id ? "var(--text)" : "var(--muted)",
              boxShadow: tab === t.id ? "inset 0 -2px 0 var(--green)" : undefined,
            }}>
            {t.label}
          </button>
        ))}
      </div>
      <div className="grow min-h-0">
        {tab === "leaderboard" ? <LeaderboardTab />
          : tab === "backtester" ? <BacktesterPage />
          : tab === "edge" ? <EdgePage />
          : tab === "exits" ? <ExitLabPage />
          : tab === "gauntlet" ? <GauntletPage />
          : tab === "rules" ? <RulesPage />
          : <TunerPage />}
      </div>
    </div>
  );
}
