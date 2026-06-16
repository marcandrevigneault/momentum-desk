import { useEffect, useState } from "react";
import { evaluateConfig, getTunerMeta } from "../api";

/** Live variable editor (#6): change a model's entry filters + exit and see the
 *  backtest metrics re-score instantly (off a precomputed per-event cache). Lets
 *  you actually *feel* how each variable moves the edge. */

type Metrics = { n: number; expectancy_r: number; win_rate: number; profit_factor: number; daily_sharpe: number };
const rColor = (v: number) => (v >= 0 ? "var(--green)" : "var(--red)");

function Sel({ label, value, opts, onChange }: { label: string; value: string; opts: [string, string][]; onChange: (v: string) => void }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} className="mono text-[12px] px-2 py-1.5 rounded"
        style={{ background: "var(--panel)", border: "1px solid var(--line)", color: "var(--text)" }}>
        {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
}

function Card({ label, value, color, sub }: { label: string; value: string; color?: string; sub?: string }) {
  return (
    <div className="rounded-lg px-3 py-2" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
      <div className="text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>{label}</div>
      <div className="mono text-[18px] font-bold" style={{ color: color ?? "var(--text)" }}>{value}</div>
      {sub && <div className="text-[10px] mono" style={{ color: "var(--muted)" }}>{sub}</div>}
    </div>
  );
}

export default function TunerPage() {
  const [meta, setMeta] = useState<{ sessions: string[]; policies: string[]; days: number | null; available: boolean } | null>(null);
  const [session, setSession] = useState("intraday");
  const [maxExt, setMaxExt] = useState("off");
  const [rvolMin, setRvolMin] = useState("0");
  const [rvolMax, setRvolMax] = useState("off");
  const [minMove, setMinMove] = useState("0");
  const [exit, setExit] = useState("pct_trail_10");
  const [m, setM] = useState<Metrics | null>(null);
  const [base, setBase] = useState<Metrics | null>(null);

  useEffect(() => { getTunerMeta().then((mt) => { setMeta(mt); if (mt.sessions[0]) setSession(mt.sessions[0]); }); }, []);

  // baseline (all entries, pct_trail_10) for the current session — the reference
  useEffect(() => {
    evaluateConfig({ session, max_ext: null, rvol_min: 0, rvol_max: null, min_move: 0, exit: "pct_trail_10" }).then(setBase);
  }, [session]);

  // re-score on any variable change
  useEffect(() => {
    evaluateConfig({
      session, exit,
      max_ext: maxExt === "off" ? null : Number(maxExt),
      rvol_min: Number(rvolMin),
      rvol_max: rvolMax === "off" ? null : Number(rvolMax),
      min_move: Number(minMove),
    }).then(setM);
  }, [session, maxExt, rvolMin, rvolMax, minMove, exit]);

  if (meta && !meta.available) {
    return <div className="p-6 text-[13px]" style={{ color: "var(--muted)" }}>No eval cache yet — run scripts.build_eval_cache.</div>;
  }
  const delta = (a?: number, b?: number) => (a == null || b == null ? "" : `${a - b >= 0 ? "+" : ""}${(a - b).toFixed(3)} vs all`);

  return (
    <div className="h-full overflow-auto p-5">
      <h2 className="text-[18px] font-bold mb-1">Variable tuner</h2>
      <p className="text-[12px] mb-4 max-w-3xl" style={{ color: "var(--muted)" }}>
        Change the entry filters and exit; the backtest re-scores <b>instantly</b> ({meta?.days ?? "?"} days,
        off a precomputed per-event cache). Watch how each variable moves expectancy, profit factor and Sharpe —
        and how it trades quantity (n) for quality. Compared against "all entries · 10% trail".
      </p>

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 mb-5">
        <Sel label="session" value={session} opts={(meta?.sessions ?? ["intraday"]).map((s) => [s, s])} onChange={setSession} />
        <Sel label="max extension %" value={maxExt} opts={[["off", "off"], ["15", "≤15"], ["10", "≤10"], ["8", "≤8"], ["6", "≤6"]]} onChange={setMaxExt} />
        <Sel label="min RVOL" value={rvolMin} opts={[["0", "0"], ["2", "≥2"], ["3", "≥3"], ["5", "≥5"]]} onChange={setRvolMin} />
        <Sel label="max RVOL (cap)" value={rvolMax} opts={[["off", "off"], ["50", "≤50"], ["20", "≤20"], ["10", "≤10"]]} onChange={setRvolMax} />
        <Sel label="min move %" value={minMove} opts={[["0", "0"], ["3", "≥3"], ["5", "≥5"], ["8", "≥8"]]} onChange={setMinMove} />
        <Sel label="exit policy" value={exit} opts={(meta?.policies ?? ["pct_trail_10"]).map((p) => [p, p])} onChange={setExit} />
      </div>

      {m && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <Card label="trades (n)" value={String(m.n)} sub={base ? `all: ${base.n}` : ""} />
          <Card label="expectancy" value={`${m.expectancy_r >= 0 ? "+" : ""}${m.expectancy_r.toFixed(3)}R`} color={rColor(m.expectancy_r)} sub={delta(m.expectancy_r, base?.expectancy_r)} />
          <Card label="win rate" value={`${(m.win_rate * 100).toFixed(1)}%`} />
          <Card label="profit factor" value={m.profit_factor >= 999 ? "∞" : m.profit_factor.toFixed(2)} color={m.profit_factor >= 2 ? "var(--green)" : "var(--text)"} />
          <Card label="daily Sharpe" value={`${m.daily_sharpe >= 0 ? "+" : ""}${m.daily_sharpe.toFixed(3)}`} color={rColor(m.daily_sharpe)} sub={delta(m.daily_sharpe, base?.daily_sharpe)} />
        </div>
      )}
      <p className="text-[11px] mt-5 mono" style={{ color: "var(--muted)" }}>
        Try it: cap RVOL ≤10 and pick fixed_3r → PF and win-rate jump (the edge the optimizer found). In-sample;
        magnitudes are upper bounds. Not advice.
      </p>
    </div>
  );
}
