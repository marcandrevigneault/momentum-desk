import { useEffect, useState } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { getCombos, getSimRun } from "../api";
import type { SimRun } from "../types";

/** End-to-end account simulation — pick a strategy or combo (detect → size →
 *  enter → trail → exit) with real capital, concurrency and the liquidity guard.
 *  The number that actually matters: what it would have done to the account. */

const money = (v: number) => v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const rColor = (v: number) => (v >= 0 ? "var(--green)" : "var(--red)");

// selectable account simulations — single strategies (from /api/simrun) and
// combos (from /api/combos, already account-level). Combos lack stress/exit_policy.
const STRATS: { id: string; label: string; load: () => Promise<SimRun> }[] = [
  { id: "intraday-1y", label: "Intraday · 1y", load: () => getSimRun("1y") },
  { id: "intraday-5y", label: "Intraday · 5y", load: () => getSimRun("5y") },
  { id: "combo-intraday", label: "Combo: Intraday only", load: () => getCombos().then((c) => c.combos.intraday as unknown as SimRun) },
  { id: "combo-premkt", label: "Combo: Premarket + Intraday", load: () => getCombos().then((c) => c.combos.premkt_intraday as unknown as SimRun) },
  { id: "combo-three", label: "Combo: 3-leg (+fade)", load: () => getCombos().then((c) => c.combos.three_leg as unknown as SimRun) },
];

function Stat({ label, value, color, sub }: { label: string; value: string; color?: string; sub?: string }) {
  return (
    <div className="rounded-lg px-3 py-2" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
      <div className="text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>{label}</div>
      <div className="mono text-[16px] font-bold" style={{ color: color ?? "var(--text)" }}>{value}</div>
      {sub && <div className="text-[10px] mono" style={{ color: "var(--muted)" }}>{sub}</div>}
    </div>
  );
}

export default function SimulationPage() {
  const [sim, setSim] = useState<SimRun | null>(null);
  const [err, setErr] = useState(false);
  const [strat, setStrat] = useState("intraday-1y");
  const [month, setMonth] = useState<string | null>(null);

  useEffect(() => {
    setSim(null);
    setErr(false);
    setMonth(null);
    (STRATS.find((s) => s.id === strat) ?? STRATS[0]).load().then(setSim).catch(() => setErr(true));
  }, [strat]);

  if (err) return <div className="p-6 text-[13px]" style={{ color: "var(--red)" }}>Failed to load simulation.</div>;
  if (!sim) return <div className="p-6 text-[13px]" style={{ color: "var(--muted)" }}>Loading simulation…</div>;
  if (!sim.trades?.length) return <div className="p-6 text-[13px]" style={{ color: "var(--muted)" }}>No simulation has been run yet.</div>;

  const m = sim.metrics;
  const equity = sim.daily_equity.map((d) => ({ date: d.date, equity: d.equity }));
  const fillRate = sim.n_signals ? sim.n_taken / sim.n_signals : 0;
  // month-filtered trades when a month bar is clicked, else the most recent 40
  const shown = month
    ? sim.trades.filter((t) => t.day.startsWith(month)).slice().reverse()
    : sim.trades.slice(-40).reverse();

  return (
    <div className="h-full overflow-auto p-5">
      <div className="flex items-center gap-3 mb-1">
        <h2 className="text-[18px] font-bold m-0">Account simulation</h2>
        <span className="badge" style={{ color: "var(--muted)", borderColor: "var(--line)" }}>{sim.source ?? "snapshot"}</span>
        <span className="mono text-[11px]" style={{ color: "var(--muted)" }}>
          {sim.session ? `${sim.session} · ${sim.exit_policy}` : (sim.legs?.join(" + ") ?? "combo")} · {sim.days} days
        </span>
        <select value={strat} onChange={(e) => setStrat(e.target.value)} className="ml-auto mono text-[11px] px-2 py-1 rounded"
          style={{ background: "var(--panel)", border: "1px solid var(--line)", color: "var(--text)" }}>
          {STRATS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
        </select>
      </div>
      <p className="text-[12px] mb-4 max-w-3xl" style={{ color: "var(--muted)" }}>
        Any strategy or combo run like a real book: detect candidates, size each by the risk engine
        (<b>1% of the book risked per trade</b>, capped at 25% of equity per name + the liquidity guard),
        cap concurrent positions and the capital deployed, exit on the chosen stop, honour the daily-loss
        breaker, commissions and slippage. The trade log's <b>Size %</b> is notional as a % of the book.
        Sizing is <b>fixed</b> (does not compound) — this is the account-level result, not per-trade R.
      </p>

      {/* headline stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-2.5 mb-5">
        <Stat label="equity" value={`${money(sim.starting_equity)} → ${money(sim.final_equity)}`} color={rColor(m.return_pct)} sub={`${m.return_pct >= 0 ? "+" : ""}${m.return_pct.toFixed(1)}%`} />
        <Stat label="trades" value={String(m.trades)} sub={`${sim.n_signals} signals · ${(fillRate * 100).toFixed(0)}% taken`} />
        <Stat label="win rate" value={`${m.win_rate.toFixed(1)}%`} />
        <Stat label="profit factor" value={String(m.profit_factor)} />
        <Stat label="expectancy" value={`${m.expectancy_r >= 0 ? "+" : ""}${m.expectancy_r.toFixed(3)}R`} color={rColor(m.expectancy_r)} sub={money(m.expectancy)} />
        <Stat label="max drawdown" value={`${m.max_drawdown_pct.toFixed(1)}%`} color="var(--amber)" sub={money(-m.max_drawdown)} />
      </div>

      {/* honest caveat + slippage sensitivity */}
      <div className="rounded-xl p-3 mb-5" style={{ border: "1px solid var(--amber)", background: "rgba(251,191,36,.05)" }}>
        <div className="text-[11px] mb-2" style={{ color: "var(--amber)" }}>
          ⚠ The headline % is large because it's measured against the small $25k base (sizing is <b>fixed</b>,
          not compounding) and the fills are optimistic (0.3% slippage, <b>no halts modeled</b> — these names
          halt constantly), plus universe survivorship. The trustworthy signal is the expectancy and
          month-to-month consistency, not the dollar figure.{sim.stress ? " Slippage sensitivity (same year):" : ""}
        </div>
        {sim.stress && (
          <table className="w-full text-[12px] mono">
            <thead>
              <tr className="text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>
                <th className="text-left py-1">slippage</th><th className="text-right">return</th>
                <th className="text-right">win%</th><th className="text-right">PF</th>
                <th className="text-right">expectancy</th><th className="text-right">max DD</th>
              </tr>
            </thead>
            <tbody>
              {sim.stress.map((r) => (
                <tr key={r.slippage_pct} style={{ borderTop: "1px solid var(--line)" }}>
                  <td className="py-1">{r.slippage_pct.toFixed(1)}%{r.slippage_pct === 0.3 ? " (base)" : ""}</td>
                  <td className="text-right" style={{ color: rColor(r.return_pct) }}>{r.return_pct >= 0 ? "+" : ""}{r.return_pct.toFixed(0)}%</td>
                  <td className="text-right">{r.win_rate.toFixed(1)}%</td>
                  <td className="text-right" style={{ color: r.profit_factor >= 1.5 ? "var(--green)" : "var(--amber)" }}>{r.profit_factor.toFixed(2)}</td>
                  <td className="text-right" style={{ color: rColor(r.expectancy_r) }}>{r.expectancy_r >= 0 ? "+" : ""}{r.expectancy_r.toFixed(3)}R</td>
                  <td className="text-right" style={{ color: "var(--amber)" }}>{r.max_drawdown_pct.toFixed(1)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* equity curve */}
      <div className="rounded-xl p-3 mb-5" style={{ border: "1px solid var(--line)", height: 280 }}>
        <div className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--muted)" }}>Equity curve (end of day)</div>
        <ResponsiveContainer width="100%" height="92%">
          <AreaChart data={equity} margin={{ top: 6, right: 16, bottom: 0, left: 8 }}>
            <defs>
              <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#34d399" stopOpacity={0.4} />
                <stop offset="100%" stopColor="#34d399" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#1c2230" vertical={false} />
            <XAxis dataKey="date" tick={{ fill: "#8b949e", fontSize: 10 }} minTickGap={60} />
            <YAxis tick={{ fill: "#8b949e", fontSize: 10 }} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`} width={48} />
            <Tooltip contentStyle={{ background: "#0d1117", border: "1px solid #283040", fontSize: 12 }}
              formatter={(v: number) => money(v)} />
            <Area type="monotone" dataKey="equity" stroke="#34d399" fill="url(#eq)" strokeWidth={1.5} />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* monthly P&L — click a month to see its trades */}
      <div className="rounded-xl p-3 mb-5" style={{ border: "1px solid var(--line)", height: 220 }}>
        <div className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--muted)" }}>
          Monthly P&amp;L <span style={{ opacity: 0.6 }}>· click a bar to see that month's trades</span>
        </div>
        <ResponsiveContainer width="100%" height="88%">
          <BarChart data={sim.monthly} margin={{ top: 6, right: 12, bottom: 0, left: 8 }}>
            <CartesianGrid stroke="#1c2230" vertical={false} />
            <XAxis dataKey="period" tick={{ fill: "#8b949e", fontSize: 10 }} minTickGap={20} />
            <YAxis tick={{ fill: "#8b949e", fontSize: 10 }} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`} width={48} />
            <Tooltip contentStyle={{ background: "#0d1117", border: "1px solid #283040", fontSize: 12 }}
              formatter={(v: number) => money(v)} cursor={{ fill: "rgba(255,255,255,.04)" }} />
            <Bar dataKey="pnl" cursor="pointer"
              onClick={(d: { period?: string }) => setMonth((cur) => (cur === d.period ? null : d.period ?? null))}>
              {sim.monthly.map((r, i) => (
                <Cell key={i} fill={r.pnl >= 0 ? "#34d399" : "#f87171"}
                  fillOpacity={month && r.period !== month ? 0.3 : 1} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* trade log — month-filtered when a month is selected */}
      <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--line)" }}>
        <div className="px-4 py-2 text-[10px] uppercase tracking-wider flex items-center gap-3" style={{ color: "var(--muted)", background: "var(--panel)" }}>
          {month ? <span style={{ color: "var(--text)" }}>Trades · {month} ({shown.length})</span> : <span>Trades (last 40 of {sim.trades.length})</span>}
          {month && <button onClick={() => setMonth(null)} className="text-[10px] px-2 rounded" style={{ border: "1px solid var(--line)" }}>clear ✕</button>}
        </div>
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>
              <th className="text-left px-4 py-1.5">Day</th><th className="text-left">Sym</th>
              <th className="text-right">Entry</th><th className="text-right">Exit</th>
              <th className="text-right">Shares</th>
              <th className="text-right" title="position notional as % of the starting book">Size %</th>
              <th className="text-right">P&amp;L</th>
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
                <td className="text-right mono" style={{ color: "var(--muted)" }}>{((t.shares * t.entry / sim.starting_equity) * 100).toFixed(1)}%</td>
                <td className="text-right mono" style={{ color: rColor(t.pnl) }}>{money(t.pnl)}</td>
                <td className="text-right mono" style={{ color: rColor(t.r_multiple) }}>{t.r_multiple >= 0 ? "+" : ""}{t.r_multiple.toFixed(2)}</td>
                <td className="px-3 mono text-[11px]" style={{ color: "var(--muted)" }}>{t.exit_reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] mt-4 mono" style={{ color: "var(--muted)" }}>
        Account-level, with capacity constraints — you can't take every signal. Past simulation, not advice.
      </p>
    </div>
  );
}
