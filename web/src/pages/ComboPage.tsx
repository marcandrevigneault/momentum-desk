import { useEffect, useMemo, useState } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { getCombos } from "../api";
import type { CombosSnapshot, ComboRun } from "../types";

/** Multi-style combos: pick a combo, see its per-leg attribution, equity curve,
 *  monthly P&L, and EVERY trade (click a month to filter). */

const money = (v: number) => v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const rColor = (v: number) => (v >= 0 ? "var(--green)" : "var(--red)");
const LEG_COLORS = ["#34d399", "#60a5fa", "#fbbf24", "#f472b6"];

function Stat({ label, value, color, sub }: { label: string; value: string; color?: string; sub?: string }) {
  return (
    <div className="rounded-lg px-3 py-2" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
      <div className="text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>{label}</div>
      <div className="mono text-[15px] font-bold" style={{ color: color ?? "var(--text)" }}>{value}</div>
      {sub && <div className="text-[10px] mono" style={{ color: "var(--muted)" }}>{sub}</div>}
    </div>
  );
}

function ComboView({ c }: { c: ComboRun }) {
  const [month, setMonth] = useState<string | null>(null);
  const m = c.metrics;
  const equity = c.daily_equity.map((d) => ({ date: d.date, equity: d.equity }));
  const totalPnl = Object.values(c.leg_pnl).reduce((a, b) => a + b, 0) || 1;
  const shown = useMemo(() => {
    const t = c.trades ?? [];
    return month ? t.filter((x) => x.day.startsWith(month)).slice().reverse() : t.slice(-40).reverse();
  }, [c.trades, month]);

  return (
    <>
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-2.5 mb-4">
        <Stat label="equity" value={`${money(c.starting_equity)}→${money(c.final_equity)}`} color={rColor(m.return_pct)} sub={`${m.return_pct >= 0 ? "+" : ""}${m.return_pct.toFixed(0)}%`} />
        <Stat label="trades" value={String(m.trades)} sub={`${c.n_signals} sig · ${c.n_skipped_capacity} skip`} />
        <Stat label="win rate" value={`${m.win_rate.toFixed(1)}%`} />
        <Stat label="profit factor" value={String(m.profit_factor)} />
        <Stat label="expectancy" value={`${m.expectancy_r >= 0 ? "+" : ""}${m.expectancy_r.toFixed(3)}R`} color={rColor(m.expectancy_r)} />
        <Stat label="max drawdown" value={`${m.max_drawdown_pct.toFixed(1)}%`} color="var(--amber)" sub={money(-m.max_drawdown)} />
      </div>

      {/* per-leg attribution */}
      <div className="rounded-xl p-4 mb-4" style={{ border: "1px solid var(--line)" }}>
        <div className="text-[10px] uppercase tracking-wider mb-3" style={{ color: "var(--muted)" }}>Per-leg attribution</div>
        {c.legs.map((leg, i) => {
          const pnl = c.leg_pnl[leg] ?? 0;
          const tr = c.leg_trades[leg] ?? 0;
          return (
            <div key={leg} className="flex items-center gap-3 mb-2">
              <div className="w-24 mono text-[12px] font-semibold capitalize" style={{ color: LEG_COLORS[i % 4] }}>{leg}</div>
              <div className="grow h-5 rounded overflow-hidden" style={{ background: "var(--panel)" }}>
                <div style={{ width: `${Math.max(2, Math.abs((pnl / totalPnl) * 100))}%`, height: "100%", background: LEG_COLORS[i % 4], opacity: 0.85 }} />
              </div>
              <div className="mono text-[12px] w-28 text-right" style={{ color: rColor(pnl) }}>{money(pnl)}</div>
              <div className="mono text-[11px] w-40 text-right" style={{ color: "var(--muted)" }}>{tr} tr · {money(tr ? pnl / tr : 0)}/tr</div>
            </div>
          );
        })}
      </div>

      <div className="grid lg:grid-cols-2 gap-4 mb-4">
        <div className="rounded-xl p-3" style={{ border: "1px solid var(--line)", height: 240 }}>
          <div className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--muted)" }}>Equity curve</div>
          <ResponsiveContainer width="100%" height="90%">
            <AreaChart data={equity} margin={{ top: 6, right: 12, bottom: 0, left: 4 }}>
              <defs><linearGradient id="ceq" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#34d399" stopOpacity={0.4} /><stop offset="100%" stopColor="#34d399" stopOpacity={0} /></linearGradient></defs>
              <CartesianGrid stroke="#1c2230" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: "#8b949e", fontSize: 9 }} minTickGap={50} />
              <YAxis tick={{ fill: "#8b949e", fontSize: 9 }} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`} width={42} />
              <Tooltip contentStyle={{ background: "#0d1117", border: "1px solid #283040", fontSize: 12 }} formatter={(v: number) => money(v)} />
              <Area type="monotone" dataKey="equity" stroke="#34d399" fill="url(#ceq)" strokeWidth={1.5} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        <div className="rounded-xl p-3" style={{ border: "1px solid var(--line)", height: 240 }}>
          <div className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--muted)" }}>Monthly P&amp;L · click a bar</div>
          <ResponsiveContainer width="100%" height="90%">
            <BarChart data={c.monthly} margin={{ top: 6, right: 12, bottom: 0, left: 4 }}>
              <CartesianGrid stroke="#1c2230" vertical={false} />
              <XAxis dataKey="period" tick={{ fill: "#8b949e", fontSize: 9 }} minTickGap={16} />
              <YAxis tick={{ fill: "#8b949e", fontSize: 9 }} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`} width={42} />
              <Tooltip contentStyle={{ background: "#0d1117", border: "1px solid #283040", fontSize: 12 }} formatter={(v: number) => money(v)} cursor={{ fill: "rgba(255,255,255,.04)" }} />
              <Bar dataKey="pnl" cursor="pointer" onClick={(d: { period?: string }) => setMonth((cur) => (cur === d.period ? null : d.period ?? null))}>
                {c.monthly.map((r, i) => <Cell key={i} fill={r.pnl >= 0 ? "#34d399" : "#f87171"} fillOpacity={month && r.period !== month ? 0.3 : 1} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* trade log */}
      <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--line)" }}>
        <div className="px-4 py-2 text-[10px] uppercase tracking-wider flex items-center gap-3" style={{ color: "var(--muted)", background: "var(--panel)" }}>
          {month ? <span style={{ color: "var(--text)" }}>Trades · {month} ({shown.length})</span> : <span>Trades (last 40 of {c.trades?.length ?? 0})</span>}
          {month && <button onClick={() => setMonth(null)} className="text-[10px] px-2 rounded" style={{ border: "1px solid var(--line)" }}>clear ✕</button>}
        </div>
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>
              <th className="text-left px-4 py-1.5">Day</th><th className="text-left">Sym · leg</th>
              <th className="text-right">Entry</th><th className="text-right">Exit</th>
              <th className="text-right">Shares</th><th className="text-right">P&amp;L</th>
              <th className="text-right">R</th><th className="text-left px-3">Exit</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((t, i) => (
              <tr key={i} style={{ borderTop: "1px solid var(--line)" }}>
                <td className="px-4 py-1 mono" style={{ color: "var(--muted)" }}>{t.day}</td>
                <td className="mono font-semibold">{t.symbol}</td>
                <td className="text-right mono">${t.entry.toFixed(2)}</td>
                <td className="text-right mono">${t.exit.toFixed(2)}</td>
                <td className="text-right mono" style={{ color: "var(--muted)" }}>{t.shares}</td>
                <td className="text-right mono" style={{ color: rColor(t.pnl) }}>{money(t.pnl)}</td>
                <td className="text-right mono" style={{ color: rColor(t.r_multiple) }}>{t.r_multiple >= 0 ? "+" : ""}{t.r_multiple.toFixed(2)}</td>
                <td className="px-3 mono text-[11px]" style={{ color: "var(--muted)" }}>{t.exit_reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export default function ComboPage() {
  const [snap, setSnap] = useState<CombosSnapshot | null>(null);
  const [err, setErr] = useState(false);
  const [sel, setSel] = useState<string | null>(null);

  useEffect(() => { getCombos().then(setSnap).catch(() => setErr(true)); }, []);

  if (err) return <div className="p-6 text-[13px]" style={{ color: "var(--red)" }}>Failed to load combos.</div>;
  if (!snap) return <div className="p-6 text-[13px]" style={{ color: "var(--muted)" }}>Loading combos…</div>;
  const keys = Object.keys(snap.combos ?? {});
  if (!keys.length) return <div className="p-6 text-[13px]" style={{ color: "var(--muted)" }}>No combos have been run yet.</div>;
  const active = sel && snap.combos[sel] ? sel : keys[0];
  const combo = snap.combos[active];

  return (
    <div className="h-full overflow-auto p-5">
      <div className="flex items-center gap-3 mb-1 flex-wrap">
        <h2 className="text-[18px] font-bold m-0">Multi-style combos</h2>
        <span className="badge" style={{ color: "var(--muted)", borderColor: "var(--line)" }}>{snap.source}</span>
        <span className="mono text-[11px]" style={{ color: "var(--muted)" }}>{snap.days ?? "?"} days</span>
      </div>
      <p className="text-[12px] mb-3 max-w-3xl" style={{ color: "var(--muted)" }}>
        Pick a combo. Per-leg attribution shows whether the styles diversify (a blended profit factor above
        either leg alone), and you can click a month to see that month's trades. Note the fade leg loses money —
        the non-fade combo is the honest book.
      </p>
      {/* combo selector */}
      <div className="flex gap-2 mb-4 flex-wrap">
        {keys.map((k) => (
          <button key={k} onClick={() => setSel(k)} className="text-[12px] px-3 py-1.5 rounded-lg"
            style={{ background: k === active ? "var(--panel-2)" : "transparent", border: "1px solid var(--line)",
                     color: k === active ? "var(--text)" : "var(--muted)", fontWeight: k === active ? 600 : 400 }}>
            {snap.combos[k].label ?? k}
          </button>
        ))}
      </div>
      <ComboView key={active} c={combo} />
      <p className="text-[11px] mt-4 mono" style={{ color: "var(--muted)" }}>
        Multi-style diversifies <i>style</i> risk, not the structural caveats (fat tails, no halts, survivorship).
        Returns are upper bounds. Not advice.
      </p>
    </div>
  );
}
