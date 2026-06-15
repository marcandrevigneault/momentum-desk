import { useEffect, useRef, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { deleteRun, getRun, launchRealBacktest, listJobs, listRuns, pollJob, runBacktest } from "../api";
import type { BacktestRun, Job, PeriodRow, RunSummary } from "../types";

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
  const [feed, setFeed] = useState<"synthetic" | "real">("synthetic");
  const [busy, setBusy] = useState(false);
  const [run, setRun] = useState<BacktestRun | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [showRuns, setShowRuns] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const openedRef = useRef<Set<string>>(new Set());

  const params = { session, days, target_r: targetR, slippage_pct: slippage, max_hold: maxHold, time_exit_tod: timeExit };

  const refreshRuns = () => listRuns().then(setRuns).catch(() => {});
  useEffect(() => { refreshRuns(); }, []);

  // poll all jobs (so concurrent runs show in the side panel); auto-open the
  // result of any job that finishes, and refresh the saved-runs list
  useEffect(() => {
    const tick = async () => {
      let js: Job[] = [];
      try { js = await listJobs(); } catch { return; }
      setJobs(js);
      const justDone = js.find((j) => j.status === "done" && !openedRef.current.has(j.id));
      if (justDone) {
        openedRef.current.add(justDone.id);
        try {
          const j = await pollJob(justDone.id);
          if (j.result) setRun(j.result);
        } catch { /* ignore */ }
        refreshRuns();
      }
    };
    const t = setInterval(tick, 2000);
    return () => clearInterval(t);
  }, []);

  const go = async () => {
    setNote(null);
    if (feed === "synthetic") {
      setBusy(true);
      try { setRun(await runBacktest(params)); } finally { setBusy(false); refreshRuns(); }
      return;
    }
    // real data: fire-and-forget an async job — non-blocking, so you can launch
    // several at once. The poller + side panel track them.
    const launched = await launchRealBacktest(params);
    if (!launched.ok) setNote(launched.error ?? "could not launch real backtest");
    else { setNote(null); setJobs(await listJobs().catch(() => jobs)); }
  };

  const openRun = async (id: string) => {
    setBusy(true);
    setShowRuns(false);
    try {
      const result = await getRun(id);
      if (result) { setRun(result); setNote(null); }
      else setNote("that run could not be loaded");
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
        <Field label="Data">
          <select className={inputCls} style={inputStyle} value={feed} onChange={(e) => setFeed(e.target.value as "synthetic" | "real")}>
            <option value="synthetic">synthetic (instant)</option>
            <option value="real">real — Massive</option>
          </select>
        </Field>
        <Field label="Session">
          <select className={inputCls} style={inputStyle} value={session} onChange={(e) => setSession(e.target.value)}>
            <option value="premarket">pre-market (4:00–9:30, hold into open)</option>
            <option value="regular">regular hours</option>
          </select>
        </Field>
        <Field label={feed === "real" ? "Days (≤1300)" : "Days (≤120)"}>
          <input className={`${inputCls} w-24`} style={inputStyle} type="number" value={days} min={5} max={feed === "real" ? 1300 : 120} onChange={(e) => setDays(+e.target.value)} />
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
        <button className="btn" onClick={() => { setShowRuns((v) => !v); refreshRuns(); }} title="Browse saved backtest runs">
          🗂 Runs ({runs.length})
        </button>
        {note && <span className="mono text-[11px]" style={{ color: "var(--amber)" }}>{note}</span>}
      </div>

      {showRuns && (
        <div className="px-4 py-2 shrink-0" style={{ background: "var(--panel)", borderBottom: "1px solid var(--line)", maxHeight: 220, overflow: "auto" }}>
          {runs.length === 0 ? (
            <div className="text-[12px] py-2" style={{ color: "var(--muted)" }}>
              No saved runs yet — run a backtest (synthetic or real) and it'll be saved here.
            </div>
          ) : (
            <table className="w-full border-collapse mono text-[12px]">
              <thead style={{ color: "var(--muted)" }}>
                <tr style={{ borderBottom: "1px solid var(--line)" }}>
                  <th className="text-left px-2 py-1">When</th><th className="text-left px-2 py-1">Kind</th>
                  <th className="text-left px-2 py-1">Session</th><th className="text-right px-2 py-1">Days</th>
                  <th className="text-right px-2 py-1">Trades</th><th className="text-right px-2 py-1">R/trade</th>
                  <th className="text-right px-2 py-1">P&L</th><th className="text-right px-2 py-1">MaxDD</th>
                  <th className="px-2 py-1"></th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id} onClick={() => openRun(r.id)} className="cursor-pointer"
                      style={{ borderBottom: "1px solid var(--line)" }}
                      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel-2)")}
                      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
                    <td className="px-2 py-1" style={{ color: "var(--muted)" }}>
                      {r.ts ? new Date(r.ts * 1000).toLocaleString() : "—"}
                    </td>
                    <td className="px-2 py-1">
                      <span className="flag" style={{ background: r.synthetic ? "rgba(139,148,158,.15)" : "rgba(52,211,153,.15)", color: r.synthetic ? "var(--muted)" : "var(--green)" }}>
                        {r.synthetic ? "synthetic" : "real"}
                      </span>
                    </td>
                    <td className="px-2 py-1">{r.session}</td>
                    <td className="px-2 py-1 text-right">{r.days}</td>
                    <td className="px-2 py-1 text-right">{r.trades}</td>
                    <td className="px-2 py-1 text-right" style={{ color: (r.expectancy_r ?? 0) >= 0 ? "var(--green)" : "var(--red)" }}>
                      {(r.expectancy_r ?? 0) >= 0 ? "+" : ""}{(r.expectancy_r ?? 0).toFixed(3)}
                    </td>
                    <td className="px-2 py-1 text-right font-bold" style={{ color: (r.total_pnl ?? 0) >= 0 ? "var(--green)" : "var(--red)" }}>
                      {(r.total_pnl ?? 0) >= 0 ? "+" : ""}{(r.total_pnl ?? 0).toFixed(0)}
                    </td>
                    <td className="px-2 py-1 text-right" style={{ color: "var(--amber)" }}>{(r.max_drawdown_pct ?? 0).toFixed(1)}%</td>
                    <td className="px-2 py-1 text-right">
                      {r.id !== "realrun" && (
                        <button
                          className="px-1.5"
                          title="delete this run"
                          style={{ color: "var(--muted)", background: "transparent", border: "none", cursor: "pointer" }}
                          onClick={async (e) => { e.stopPropagation(); await deleteRun(r.id); refreshRuns(); }}
                        >✕</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      <div className="grow min-h-0 flex">
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
       <aside className="shrink-0 overflow-auto" style={{ width: 264, borderLeft: "1px solid var(--line)", background: "var(--panel)" }}>
         <div className="section-title px-3 py-2">
           Running backtests ({jobs.filter((j) => j.status === "running").length})
         </div>
         {jobs.length === 0 ? (
           <div className="px-3 py-2 text-[12px]" style={{ color: "var(--muted)" }}>
             None running. Pick <b>Data: real — Massive</b> and Run — you can launch several at once.
           </div>
         ) : (
           jobs.map((j) => {
             const pct = Math.round((j.status === "done" ? 1 : j.progress) * 100);
             const col = j.status === "running" ? "var(--amber)" : j.status === "error" ? "var(--red)" : "var(--green)";
             return (
               <div key={j.id} className="px-3 py-2" style={{ borderBottom: "1px solid var(--line)" }}>
                 <div className="flex justify-between mono text-[11px]">
                   <span>{j.params.session} · {j.params.days}d · {j.params.target_r}R</span>
                   <span style={{ color: col }}>{j.status}</span>
                 </div>
                 <div className="mt-1.5" style={{ height: 6, background: "var(--panel-2)", borderRadius: 99, overflow: "hidden" }}>
                   <div style={{ height: "100%", width: `${pct}%`, background: col, transition: "width .3s" }} />
                 </div>
                 <div className="mt-1 mono text-[10px]" style={{ color: "var(--muted)" }}>
                   {j.status === "running" ? `${pct}% · ${Math.round(j.elapsed)}s`
                     : j.status === "error" ? (j.error ?? "failed")
                     : `done · ${Math.round(j.elapsed)}s`}
                 </div>
               </div>
             );
           })
         )}
       </aside>
      </div>
    </div>
  );
}
