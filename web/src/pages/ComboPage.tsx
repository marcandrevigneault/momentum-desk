import { useEffect, useState } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { getCombo } from "../api";
import type { ComboRun } from "../types";

/** Multi-style combo: several strategy legs in one shared-capital book, with
 *  per-leg P&L attribution to show whether the styles diversify. */

const money = (v: number) => v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const rColor = (v: number) => (v >= 0 ? "var(--green)" : "var(--red)");
const LEG_COLORS = ["#34d399", "#60a5fa", "#fbbf24", "#f472b6"];

function Stat({ label, value, color, sub }: { label: string; value: string; color?: string; sub?: string }) {
  return (
    <div className="rounded-lg px-3 py-2" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
      <div className="text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>{label}</div>
      <div className="mono text-[16px] font-bold" style={{ color: color ?? "var(--text)" }}>{value}</div>
      {sub && <div className="text-[10px] mono" style={{ color: "var(--muted)" }}>{sub}</div>}
    </div>
  );
}

export default function ComboPage() {
  const [c, setC] = useState<ComboRun | null>(null);
  const [err, setErr] = useState(false);
  const [win, setWin] = useState("1y");

  useEffect(() => {
    setC(null);
    setErr(false);
    getCombo(win).then(setC).catch(() => setErr(true));
  }, [win]);

  if (err) return <div className="p-6 text-[13px]" style={{ color: "var(--red)" }}>Failed to load combo.</div>;
  if (!c) return <div className="p-6 text-[13px]" style={{ color: "var(--muted)" }}>Loading combo…</div>;
  if (!c.daily_equity?.length) return <div className="p-6 text-[13px]" style={{ color: "var(--muted)" }}>No combo has been run yet.</div>;

  const m = c.metrics;
  const equity = c.daily_equity.map((d) => ({ date: d.date, equity: d.equity }));
  const totalPnl = Object.values(c.leg_pnl).reduce((a, b) => a + b, 0) || 1;
  const fillRate = c.n_signals ? c.n_taken / c.n_signals : 0;

  return (
    <div className="h-full overflow-auto p-5">
      <div className="flex items-center gap-3 mb-1 flex-wrap">
        <h2 className="text-[18px] font-bold m-0">Multi-style combo</h2>
        <span className="badge" style={{ color: "var(--muted)", borderColor: "var(--line)" }}>{c.source}</span>
        <span className="mono text-[11px]" style={{ color: "var(--muted)" }}>{c.legs.join(" + ")} · {c.days} days</span>
        <div className="ml-auto flex rounded-md overflow-hidden" style={{ border: "1px solid var(--line)" }}>
          {["1y", "5y"].map((w) => (
            <button key={w} onClick={() => setWin(w)} className="mono text-[11px] px-3 py-1"
              style={{ background: win === w ? "var(--panel-2)" : "transparent", color: win === w ? "var(--text)" : "var(--muted)" }}>
              {w}
            </button>
          ))}
        </div>
      </div>
      <p className="text-[12px] mb-4 max-w-3xl" style={{ color: "var(--muted)" }}>
        {c.config ?? "Several strategy legs share one account"} — shared equity, the concurrency cap and the
        daily-loss breaker. Per-leg attribution shows whether the styles actually <i>diversify</i>: a blended
        profit factor above either leg alone means their losing stretches don't fully overlap. The trade-off is
        capacity — the legs compete for the same slots.
      </p>

      {/* headline stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-2.5 mb-5">
        <Stat label="equity" value={`${money(c.starting_equity)} → ${money(c.final_equity)}`} color={rColor(m.return_pct)} sub={`${m.return_pct >= 0 ? "+" : ""}${m.return_pct.toFixed(1)}%`} />
        <Stat label="trades" value={String(m.trades)} sub={`${c.n_signals} sig · ${(fillRate * 100).toFixed(0)}% taken · ${c.n_skipped_capacity} skip`} />
        <Stat label="win rate" value={`${m.win_rate.toFixed(1)}%`} />
        <Stat label="profit factor" value={String(m.profit_factor)} color="var(--green)" sub="vs legs alone" />
        <Stat label="expectancy" value={`${m.expectancy_r >= 0 ? "+" : ""}${m.expectancy_r.toFixed(3)}R`} color={rColor(m.expectancy_r)} />
        <Stat label="max drawdown" value={`${m.max_drawdown_pct.toFixed(1)}%`} color="var(--amber)" sub={money(-m.max_drawdown)} />
      </div>

      {/* per-leg attribution */}
      <div className="rounded-xl p-4 mb-5" style={{ border: "1px solid var(--line)" }}>
        <div className="text-[10px] uppercase tracking-wider mb-3" style={{ color: "var(--muted)" }}>Per-leg attribution</div>
        <div className="flex flex-col gap-3">
          {c.legs.map((leg, i) => {
            const pnl = c.leg_pnl[leg] ?? 0;
            const trades = c.leg_trades[leg] ?? 0;
            const perTrade = trades ? pnl / trades : 0;
            const share = (pnl / totalPnl) * 100;
            return (
              <div key={leg} className="flex items-center gap-3">
                <div className="w-24 mono text-[12px] font-semibold capitalize" style={{ color: LEG_COLORS[i % 4] }}>{leg}</div>
                <div className="grow h-5 rounded overflow-hidden" style={{ background: "var(--panel)" }}>
                  <div style={{ width: `${Math.max(2, Math.abs(share))}%`, height: "100%", background: LEG_COLORS[i % 4], opacity: 0.85 }} />
                </div>
                <div className="mono text-[12px] w-28 text-right" style={{ color: rColor(pnl) }}>{money(pnl)}</div>
                <div className="mono text-[11px] w-40 text-right" style={{ color: "var(--muted)" }}>{trades} tr · {money(perTrade)}/tr</div>
              </div>
            );
          })}
        </div>
      </div>

      {/* equity curve */}
      <div className="rounded-xl p-3 mb-5" style={{ border: "1px solid var(--line)", height: 280 }}>
        <div className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--muted)" }}>Equity curve (end of day)</div>
        <ResponsiveContainer width="100%" height="92%">
          <AreaChart data={equity} margin={{ top: 6, right: 16, bottom: 0, left: 8 }}>
            <defs>
              <linearGradient id="ceq" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#34d399" stopOpacity={0.4} />
                <stop offset="100%" stopColor="#34d399" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#1c2230" vertical={false} />
            <XAxis dataKey="date" tick={{ fill: "#8b949e", fontSize: 10 }} minTickGap={60} />
            <YAxis tick={{ fill: "#8b949e", fontSize: 10 }} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`} width={48} />
            <Tooltip contentStyle={{ background: "#0d1117", border: "1px solid #283040", fontSize: 12 }} formatter={(v: number) => money(v)} />
            <Area type="monotone" dataKey="equity" stroke="#34d399" fill="url(#ceq)" strokeWidth={1.5} />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* monthly */}
      <div className="rounded-xl p-3 mb-5" style={{ border: "1px solid var(--line)", height: 220 }}>
        <div className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--muted)" }}>Monthly P&amp;L</div>
        <ResponsiveContainer width="100%" height="88%">
          <BarChart data={c.monthly} margin={{ top: 6, right: 12, bottom: 0, left: 8 }}>
            <CartesianGrid stroke="#1c2230" vertical={false} />
            <XAxis dataKey="period" tick={{ fill: "#8b949e", fontSize: 10 }} minTickGap={20} />
            <YAxis tick={{ fill: "#8b949e", fontSize: 10 }} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`} width={48} />
            <Tooltip contentStyle={{ background: "#0d1117", border: "1px solid #283040", fontSize: 12 }} formatter={(v: number) => money(v)} cursor={{ fill: "rgba(255,255,255,.04)" }} />
            <Bar dataKey="pnl">
              {c.monthly.map((r, i) => <Cell key={i} fill={r.pnl >= 0 ? "#34d399" : "#f87171"} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <p className="text-[11px] mono" style={{ color: "var(--muted)" }}>
        Multi-style diversifies <i>style</i> risk, not the structural caveats (fat tails, no halts modeled,
        universe survivorship). A <b>fade</b> leg is short — and shorting thin low-floats is often
        hard-to-borrow / recallable, so its real-world fills are worse than modeled. The headline return is an
        upper bound. Not advice.
      </p>
    </div>
  );
}
