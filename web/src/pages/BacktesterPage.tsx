import { useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { getRealRun, runBacktest } from "../api";
import type { BacktestRun, PeriodRow } from "../types";

const money = (v: number) => v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

function Metric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="rounded-lg px-3 py-2" style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}>
      <div className="text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>{label}</div>
      <div className="mono text-[16px] font-bold" style={{ color: color ?? "var(--text)" }}>{value}</div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>{label}</span>
      {children}
    </label>
  );
}

const inputCls = "mono text-[13px] rounded-md px-2 py-1.5";
const inputStyle = { background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" };

function PeriodTable({ title, rows }: { title: string; rows: PeriodRow[] }) {
  return (
    <div className="rounded-lg" style={{ background: "var(--panel)", border: "1px solid var(--line)", flex: 1, minWidth: 0 }}>
      <div className="section-title px-3 py-2">{title} ({rows.length})</div>
      <div className="overflow-auto" style={{ maxHeight: 220 }}>
        <table className="w-full border-collapse mono text-[12px]">
          <thead className="sticky top-0" style={{ background: "var(--panel)", color: "var(--muted)" }}>
            <tr style={{ borderBottom: "1px solid var(--line)" }}>
              <th className="text-left px-3 py-1.5">Period</th><th className="text-right px-3 py-1.5">Trades</th>
              <th className="text-right px-3 py-1.5">Win%</th><th className="text-right px-3 py-1.5">P&L</th>
              <th className="text-right px-3 py-1.5">Cum</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.period} style={{ borderBottom: "1px solid var(--line)" }}>
                <td className="px-3 py-1.5">{p.period}</td>
                <td className="px-3 py-1.5 text-right">{p.trades}</td>
                <td className="px-3 py-1.5 text-right">{p.win_rate.toFixed(0)}%</td>
                <td className="px-3 py-1.5 text-right font-bold" style={{ color: p.pnl >= 0 ? "var(--green)" : "var(--red)" }}>
                  {p.pnl >= 0 ? "+" : ""}{p.pnl.toFixed(0)}
                </td>
                <td className="px-3 py-1.5 text-right" style={{ color: "var(--muted)" }}>{p.cum_pnl.toFixed(0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function MonthlyBars({ rows }: { rows: PeriodRow[] }) {
  return (
    <div className="rounded-lg p-3" style={{ background: "var(--panel)", border: "1px solid var(--line)", height: 200 }}>
      <div className="section-title mb-1">Monthly P&L</div>
      <ResponsiveContainer width="100%" height="86%">
        <BarChart data={rows} margin={{ top: 6, right: 12, bottom: 0, left: 8 }}>
          <CartesianGrid stroke="#232b3a" strokeDasharray="2 4" />
          <XAxis dataKey="period" stroke="#8b949e" tick={{ fontSize: 9, fontFamily: "JetBrains Mono" }} interval="preserveStartEnd" />
          <YAxis stroke="#8b949e" tick={{ fontSize: 10, fontFamily: "JetBrains Mono" }} width={56} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`} />
          <Tooltip contentStyle={{ background: "#161b22", border: "1px solid #283040", fontFamily: "JetBrains Mono", fontSize: 11 }}
            formatter={(v: number) => [`$${v.toFixed(0)}`, "P&L"]} />
          <Bar dataKey="pnl">
            {rows.map((p) => <Cell key={p.period} fill={p.pnl >= 0 ? "#34d399" : "#f87171"} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function BacktesterPage() {
  const [session, setSession] = useState("premarket");
  const [days, setDays] = useState(60);
  const [targetR, setTargetR] = useState(2.0);
  const [slippage, setSlippage] = useState(0.5);
  const [maxHold, setMaxHold] = useState(60);
  const [timeExit, setTimeExit] = useState(630);   // 0=off, 600=10:00, 630=10:30
  const [busy, setBusy] = useState(false);
  const [run, setRun] = useState<BacktestRun | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const go = async () => {
    setBusy(true);
    setNote(null);
    try {
      setRun(await runBacktest({ session, days, target_r: targetR, slippage_pct: slippage, max_hold: maxHold, time_exit_tod: timeExit }));
    } finally {
      setBusy(false);
    }
  };

  const loadReal = async () => {
    setBusy(true);
    setNote(null);
    try {
      const r = await getRealRun();
      if (r.available) setRun(r);
      else setNote("No real run found yet — run scripts/realrun.py with your Massive key (it writes data/realrun.json).");
    } finally {
      setBusy(false);
    }
  };

  const m = run?.metrics;
  const curve = (run?.equity_curve ?? []).map((eq, i) => ({ i, eq }));

  return (
    <div className="h-full flex flex-col" style={{ background: "var(--bg)" }}>
      {/* controls */}
      <div className="flex flex-wrap items-end gap-4 px-4 py-3 shrink-0" style={{ background: "var(--panel)", borderBottom: "1px solid var(--line)" }}>
        <Field label="Session">
          <select className={inputCls} style={inputStyle} value={session} onChange={(e) => setSession(e.target.value)}>
            <option value="premarket">pre-market (4:00–9:30, hold into open)</option>
            <option value="regular">regular hours</option>
          </select>
        </Field>
        <Field label="Days">
          <input className={`${inputCls} w-20`} style={inputStyle} type="number" value={days} min={5} max={120} onChange={(e) => setDays(+e.target.value)} />
        </Field>
        <Field label="Target R">
          <input className={`${inputCls} w-20`} style={inputStyle} type="number" step={0.5} value={targetR} onChange={(e) => setTargetR(+e.target.value)} />
        </Field>
        <Field label="Slippage %">
          <input className={`${inputCls} w-20`} style={inputStyle} type="number" step={0.1} value={slippage} onChange={(e) => setSlippage(+e.target.value)} />
        </Field>
        <Field label="Max hold (min)">
          <input className={`${inputCls} w-20`} style={inputStyle} type="number" step={5} value={maxHold} onChange={(e) => setMaxHold(+e.target.value)} />
        </Field>
        <Field label="Force flat at">
          <select className={inputCls} style={inputStyle} value={timeExit} onChange={(e) => setTimeExit(+e.target.value)}>
            <option value={0}>no cap</option>
            <option value={600}>10:00 ET</option>
            <option value={630}>10:30 ET (fade)</option>
            <option value={660}>11:00 ET</option>
          </select>
        </Field>
        <button className="btn btn-buy" disabled={busy} onClick={go}>{busy ? "Running…" : "▶ Run backtest"}</button>
        <button className="btn" disabled={busy} onClick={loadReal} title="Load the latest local multi-year real-data run">↑ Load real run</button>
        {note && <span className="mono text-[11px]" style={{ color: "var(--amber)" }}>{note}</span>}
      </div>

      <div className="grow min-h-0 overflow-auto p-4">
        {!run ? (
          <div className="grid place-items-center h-full text-[13px]" style={{ color: "var(--muted)" }}>
            Set parameters and run a backtest to see the equity curve, metrics, and trades.
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            {run.synthetic && (
              <div className="text-[12px] mono px-3 py-2 rounded-lg" style={{ background: "rgba(251,191,36,.12)", color: "var(--amber)", border: "1px solid rgba(251,191,36,.3)" }}>
                ⚠ SYNTHETIC DATA — fabricated prices for engine illustration. Not strategy evidence; connect a real feed for meaningful numbers.
              </div>
            )}
            {m && (
              <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))" }}>
                <Metric label="Trades" value={String(m.trades)} />
                <Metric label="Win rate" value={`${m.win_rate.toFixed(1)}%`} />
                <Metric label="Profit factor" value={m.profit_factor.toFixed(2)} color="var(--blue)" />
                <Metric label="Expectancy / trade" value={`${m.expectancy_r >= 0 ? "+" : ""}${m.expectancy_r.toFixed(3)} R`} color={m.expectancy_r >= 0 ? "var(--green)" : "var(--red)"} />
                <Metric label="Total P&L" value={`${m.total_pnl >= 0 ? "+" : ""}${money(m.total_pnl)}`} color={m.total_pnl >= 0 ? "var(--green)" : "var(--red)"} />
                <Metric label="Return" value={`${m.return_pct >= 0 ? "+" : ""}${m.return_pct.toFixed(1)}%`} color={m.return_pct >= 0 ? "var(--green)" : "var(--red)"} />
                <Metric label="Max drawdown" value={`${m.max_drawdown_pct.toFixed(1)}%`} color="var(--amber)" />
                <Metric label="Avg win / loss" value={`+${money(m.avg_win)} / ${money(m.avg_loss)}`} />
              </div>
            )}

            <div className="rounded-lg p-3" style={{ background: "var(--panel)", border: "1px solid var(--line)", height: 280 }}>
              <div className="section-title mb-1">Equity curve · {run.session} · {run.days} days</div>
              <ResponsiveContainer width="100%" height="90%">
                <LineChart data={curve} margin={{ top: 6, right: 16, bottom: 0, left: 8 }}>
                  <CartesianGrid stroke="#232b3a" strokeDasharray="2 4" />
                  <XAxis dataKey="i" hide />
                  <YAxis stroke="#8b949e" tick={{ fontSize: 10, fontFamily: "JetBrains Mono" }} width={64}
                    domain={["auto", "auto"]} tickFormatter={(v) => `$${(v / 1000).toFixed(1)}k`} />
                  <Tooltip contentStyle={{ background: "#161b22", border: "1px solid #283040", fontFamily: "JetBrains Mono", fontSize: 11 }}
                    labelFormatter={() => ""} formatter={(v: number) => [money(v), "equity"]} />
                  <Line type="monotone" dataKey="eq" stroke="#34d399" dot={false} strokeWidth={1.6} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>

            {!!run.monthly?.length && <MonthlyBars rows={run.monthly} />}
            {(!!run.monthly?.length || !!run.yearly?.length) && (
              <div className="flex flex-wrap gap-4">
                {!!run.yearly?.length && <PeriodTable title="Year by year" rows={run.yearly} />}
                {!!run.monthly?.length && <PeriodTable title="Month by month" rows={run.monthly} />}
              </div>
            )}

            <div className="rounded-lg" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
              <div className="section-title px-3 py-2">Trades ({run.trades.length})</div>
              <div className="overflow-auto" style={{ maxHeight: 320 }}>
                <table className="w-full border-collapse mono text-[12px]">
                  <thead className="sticky top-0" style={{ background: "var(--panel)", color: "var(--muted)" }}>
                    <tr style={{ borderBottom: "1px solid var(--line)" }}>
                      <th className="text-left px-3 py-1.5">Day</th><th className="text-left px-3 py-1.5">Sym</th>
                      <th className="text-right px-3 py-1.5">Entry</th><th className="text-right px-3 py-1.5">Exit</th>
                      <th className="text-right px-3 py-1.5">Shares</th><th className="text-left px-3 py-1.5">Why</th>
                      <th className="text-right px-3 py-1.5">R</th><th className="text-right px-3 py-1.5">P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {run.trades.map((t, i) => (
                      <tr key={i} style={{ borderBottom: "1px solid var(--line)" }}>
                        <td className="px-3 py-1.5" style={{ color: "var(--muted)" }}>{t.day}</td>
                        <td className="px-3 py-1.5 font-bold" style={{ fontFamily: "Inter" }}>{t.symbol}</td>
                        <td className="px-3 py-1.5 text-right">${t.entry.toFixed(2)}</td>
                        <td className="px-3 py-1.5 text-right">${t.exit.toFixed(2)}</td>
                        <td className="px-3 py-1.5 text-right">{t.shares.toLocaleString()}</td>
                        <td className="px-3 py-1.5" style={{ color: t.exit_reason === "target" ? "var(--green)" : t.exit_reason === "stop" ? "var(--red)" : "var(--muted)" }}>{t.exit_reason}</td>
                        <td className="px-3 py-1.5 text-right">{t.r_multiple.toFixed(2)}</td>
                        <td className="px-3 py-1.5 text-right font-bold" style={{ color: t.pnl >= 0 ? "var(--green)" : "var(--red)" }}>
                          {t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(0)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
