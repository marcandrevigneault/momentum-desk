import { useEffect, useState } from "react";
import {
  getLabRun, getLabStrategies, getLeaderboard, LabStrategy, LeaderRow,
  renameLabStrategy, setLabActive,
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
const money2 = (v: number) => (v ?? 0).toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0, signDisplay: "always" });
const rColor = (v: number) => (v >= 0 ? "var(--green)" : "var(--red)");
const tod = (mins: number) => `${String(Math.floor((mins ?? 0) / 60)).padStart(2, "0")}:${String((mins ?? 0) % 60).padStart(2, "0")}`;

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

function Chip({ k, v }: { k: string; v: string }) {
  return (
    <span className="mono px-2 py-1 rounded" style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}>
      <span style={{ color: "var(--muted)" }}>{k}</span> {v}
    </span>
  );
}

function LeaderboardTab() {
  const [strategies, setStrategies] = useState<LabStrategy[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [board, setBoard] = useState<LeaderRow[]>([]);
  const [rankBy, setRankBy] = useState("expectancy_r");
  const [win, setWin] = useState("1y");
  const [selected, setSelected] = useState<any | null>(null);
  const [monthFilter, setMonthFilter] = useState<string | null>(null);

  const reloadBoard = async (rb = rankBy, w = win) => setBoard(await getLeaderboard(rb, w));
  const reloadStrats = async () => {
    const s = await getLabStrategies();
    setStrategies(s.strategies); setActive(s.active);
  };
  useEffect(() => { reloadStrats(); }, []);
  useEffect(() => { reloadBoard(rankBy, win); }, [rankBy, win]);

  const pickRow = async (r: LeaderRow) => { setMonthFilter(null); setSelected(await getLabRun(r.id)); };
  const makeActive = async (e: any, name: string) => { e.stopPropagation(); await setLabActive(name); setActive(name); };

  const sel = selected?.result;
  const m = sel?.metrics ?? {};
  const strat = strategies.find((s) => s.name === selected?.strategy) ?? null;
  const [rename, setRename] = useState("");

  const doRename = async () => {
    const next = rename.trim();
    if (!next || !strat || next === strat.name) { setRename(""); return; }
    const res = await renameLabStrategy(strat.name, next);
    if (res.ok) { await reloadStrats(); await reloadBoard(); setSelected({ ...selected, strategy: next }); }
    setRename("");
  };

  return (
    <div className="h-full overflow-auto p-4 flex flex-col gap-4">
      {/* header */}
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="text-[15px] font-bold m-0">Strategy Lab</h2>
        <span className="text-[12px]" style={{ color: "var(--muted)" }}>
          {board[0]?.data_source === "polygon" ? "real Polygon data" : board[0]?.data_source ?? "—"} · ranked · click a row for internals + trades
        </span>
        <div className="ml-auto flex items-center gap-3">
          <select value={rankBy} onChange={(e) => setRankBy(e.target.value)} className="mono text-[11px] px-2 py-1 rounded"
            style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }}>
            {RANKS.map((r) => <option key={r.k} value={r.k}>rank · {r.label}</option>)}
          </select>
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

      {/* leaderboard — one row per strategy, its cached run for the window */}
      <div className="rounded-lg" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
        <table className="w-full text-[12px]">
          <thead>
            <tr style={{ color: "var(--muted)" }} className="text-left">
              <th className="px-3 py-1.5 font-medium">#</th>
              <th className="px-3 py-1.5 font-medium"></th>
              <th className="px-3 py-1.5 font-medium">Strategy</th>
              <th className="px-3 py-1.5 font-medium mono">expR</th>
              <th className="px-3 py-1.5 font-medium mono">PF</th>
              <th className="px-3 py-1.5 font-medium mono">win%</th>
              <th className="px-3 py-1.5 font-medium mono">maxDD%</th>
              <th className="px-3 py-1.5 font-medium mono">return</th>
              <th className="px-3 py-1.5 font-medium mono">final</th>
            </tr>
          </thead>
          <tbody>
            {board.length === 0 && (
              <tr><td colSpan={9} className="px-3 py-4 text-center" style={{ color: "var(--muted)" }}>
                No runs cached for {win} yet.</td></tr>
            )}
            {board.map((r, i) => (
              <tr key={r.id} onClick={() => pickRow(r)} className="cursor-pointer"
                style={{ borderTop: "1px solid var(--line)", background: selected?.id === r.id ? "var(--panel-2)" : undefined }}>
                <td className="px-3 py-1.5 mono" style={{ color: "var(--muted)" }}>{i + 1}</td>
                <td className="px-2 py-1.5">
                  <button title="set active" onClick={(e) => makeActive(e, r.strategy)} className="text-[13px]"
                    style={{ color: active === r.strategy ? "var(--green)" : "var(--muted)" }}>★</button>
                </td>
                <td className="px-3 py-1.5">{r.strategy}</td>
                <td className="px-3 py-1.5 mono" style={{ color: rColor(r.metrics.expectancy_r) }}>{(r.metrics.expectancy_r ?? 0).toFixed(2)}</td>
                <td className="px-3 py-1.5 mono">{(r.metrics.profit_factor ?? 0).toFixed(2)}</td>
                <td className="px-3 py-1.5 mono">{(r.metrics.win_rate ?? 0).toFixed(0)}</td>
                <td className="px-3 py-1.5 mono">{(r.metrics.max_drawdown_pct ?? 0).toFixed(1)}</td>
                <td className="px-3 py-1.5 mono" style={{ color: rColor(r.metrics.return_pct) }}>{(r.metrics.return_pct ?? 0).toFixed(0)}%</td>
                <td className="px-3 py-1.5 mono">{money(r.final_equity)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* selected run — internals, metrics, monthly, trade log */}
      {sel && (
        <div className="rounded-lg p-3 flex flex-col gap-3" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
          {/* name + rename */}
          <div className="flex items-center gap-2 flex-wrap">
            <input value={rename || selected.strategy} onChange={(e) => setRename(e.target.value)}
              onBlur={doRename} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
              className="text-[14px] font-bold px-2 py-1 rounded"
              style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }}
              title="rename this strategy" />
            <span className="mono text-[11px]" style={{ color: "var(--muted)" }}>{selected.window} · {selected.data_source}</span>
          </div>

          {/* internals — what this strategy actually IS */}
          {strat && (
            <div className="flex flex-wrap gap-2 text-[11px]">
              <Chip k="kind" v={strat.kind} />
              {strat.kind === "combo"
                ? strat.legs.map((l, i) => <Chip key={i} k={`leg ${i + 1}`} v={`${l.session}·${l.style}`} />)
                : <Chip k="session" v={strat.session} />}
              <Chip k="exit" v={strat.exit_policy} />
              <Chip k="sizing" v={`${strat.sizing.mode} ${strat.sizing.risk_pct}%`} />
              <Chip k="max conc." v={String(strat.max_concurrent)} />
            </div>
          )}

          {/* headline metrics */}
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

          {/* month-to-month + trade log, side by side on wide screens */}
          <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
            <div className="rounded-lg overflow-hidden" style={{ border: "1px solid var(--line)" }}>
              <div className="px-3 py-1.5 text-[11px] font-semibold" style={{ background: "var(--panel-2)" }}>Month-to-month</div>
              <table className="w-full text-[11px]">
                <thead><tr style={{ color: "var(--muted)" }} className="text-left">
                  <th className="px-2 py-1 font-medium">Month</th><th className="px-2 py-1 font-medium mono">tr</th>
                  <th className="px-2 py-1 font-medium mono">win%</th><th className="px-2 py-1 font-medium mono">P&amp;L</th>
                  <th className="px-2 py-1 font-medium mono">cum</th></tr></thead>
                <tbody>
                  {(sel.monthly ?? []).map((r: any) => (
                    <tr key={r.period} onClick={() => setMonthFilter(monthFilter === r.period ? null : r.period)}
                      className="cursor-pointer"
                      style={{ borderTop: "1px solid var(--line)", background: monthFilter === r.period ? "var(--panel-2)" : undefined }}
                      title="click to filter the trade log to this month">
                      <td className="px-2 py-1 mono">{r.period}</td>
                      <td className="px-2 py-1 mono">{r.trades}</td>
                      <td className="px-2 py-1 mono">{r.win_rate}</td>
                      <td className="px-2 py-1 mono" style={{ color: rColor(r.pnl) }}>{money2(r.pnl)}</td>
                      <td className="px-2 py-1 mono" style={{ color: rColor(r.cum_pnl) }}>{money(r.cum_pnl)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="rounded-lg overflow-hidden flex flex-col" style={{ border: "1px solid var(--line)", maxHeight: 340 }}>
              {(() => {
                const all = (sel.trades ?? []) as any[];
                const filtered = monthFilter ? all.filter((t) => (t.day ?? "").startsWith(monthFilter)) : all;
                const shown = filtered.slice(-500).reverse();   // cap rendered rows; newest first
                return (<>
                  <div className="px-3 py-1.5 text-[11px] font-semibold shrink-0 flex items-center gap-2" style={{ background: "var(--panel-2)" }}>
                    <span>Trade log</span>
                    {monthFilter
                      ? <span className="mono px-1.5 rounded" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
                          {monthFilter} · {filtered.length}
                          <button onClick={() => setMonthFilter(null)} className="ml-1" style={{ color: "var(--muted)" }}>✕</button>
                        </span>
                      : <span style={{ color: "var(--muted)" }}>
                          ({shown.length === all.length ? all.length : `recent ${shown.length} of ${all.length}`}) — click a month to filter
                        </span>}
                  </div>
                  <div className="overflow-auto">
                    <table className="w-full text-[11px]">
                      <thead><tr style={{ color: "var(--muted)" }} className="text-left sticky top-0">
                        <th className="px-2 py-1 font-medium">Day</th><th className="px-2 py-1 font-medium">Sym</th>
                        <th className="px-2 py-1 font-medium mono">in→out</th><th className="px-2 py-1 font-medium mono">sh</th>
                        <th className="px-2 py-1 font-medium mono">R</th><th className="px-2 py-1 font-medium mono">P&amp;L</th>
                        <th className="px-2 py-1 font-medium">exit</th></tr></thead>
                      <tbody>
                        {shown.length === 0 && <tr><td colSpan={7} className="px-2 py-3 text-center" style={{ color: "var(--muted)" }}>no trades this month</td></tr>}
                        {shown.map((t: any, i: number) => (
                          <tr key={i} style={{ borderTop: "1px solid var(--line)" }}>
                        <td className="px-2 py-1 mono">{t.day}</td>
                        <td className="px-2 py-1">{t.symbol}</td>
                        <td className="px-2 py-1 mono">{tod(t.entry_tod)}→{tod(t.exit_tod)}</td>
                        <td className="px-2 py-1 mono">{t.shares}</td>
                        <td className="px-2 py-1 mono" style={{ color: rColor(t.r_multiple) }}>{(t.r_multiple ?? 0).toFixed(2)}</td>
                        <td className="px-2 py-1 mono" style={{ color: rColor(t.pnl) }}>{money2(t.pnl)}</td>
                        <td className="px-2 py-1" style={{ color: "var(--muted)" }}>{t.exit_reason}</td>
                      </tr>
                    ))}
                      </tbody>
                    </table>
                  </div>
                </>);
              })()}
            </div>
          </div>
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
