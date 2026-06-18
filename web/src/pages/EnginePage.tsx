import { useEffect, useState } from "react";
import { getLiveIntent, type LiveCandidate, type LiveIntent } from "../api";

const money = (v: number) => (v ?? 0).toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const money2 = (v: number) => (v ?? 0).toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });

const STATE = {
  watch: { c: "var(--blue, #5b8def)", t: "watching" },
  holding: { c: "var(--green)", t: "HOLDING" },
  done: { c: "var(--muted)", t: "done" },
} as const;

function Pill({ children, color }: { children: React.ReactNode; color: string }) {
  return <span className="mono text-[10px] px-2 py-0.5 rounded" style={{ color, border: `1px solid ${color}` }}>{children}</span>;
}

function CandidateRow({ c }: { c: LiveCandidate }) {
  const st = STATE[c.state as keyof typeof STATE] ?? STATE.watch;
  // how close price is to the breakout trigger (high-of-session) — the "setup"
  const toTrig = c.trigger && c.last ? (100 * (c.last - c.trigger)) / c.trigger : null;
  return (
    <tr style={{ borderBottom: "1px solid var(--line)" }}>
      <td className="px-3 py-1.5 font-semibold">{c.symbol}</td>
      <td className="px-3 py-1.5"><Pill color={st.c}>{st.t}</Pill></td>
      <td className="px-3 py-1.5 mono text-right" style={{ color: c.gap_pct >= 0 ? "var(--green)" : "var(--red)" }}>{c.gap_pct?.toFixed(1)}%</td>
      <td className="px-3 py-1.5 mono text-right">{c.last != null ? money2(c.last) : "—"}</td>
      <td className="px-3 py-1.5 mono text-right">{c.trigger != null ? money2(c.trigger) : "—"}</td>
      <td className="px-3 py-1.5 mono text-right" style={{ color: toTrig != null && toTrig >= 0 ? "var(--green)" : "var(--muted)" }}>
        {toTrig != null ? `${toTrig >= 0 ? "+" : ""}${toTrig.toFixed(1)}%` : "—"}
      </td>
      <td className="px-3 py-1.5 mono text-right">{c.bars}</td>
      <td className="px-3 py-1.5 mono text-right">{c.entry != null ? money2(c.entry) : "—"}</td>
      <td className="px-3 py-1.5 mono text-right">{c.stop != null ? money2(c.stop) : "—"}</td>
      <td className="px-3 py-1.5 mono text-right">{c.exit != null ? money2(c.exit) : "—"}</td>
      <td className="px-3 py-1.5 mono text-right" style={{ color: (c.pnl ?? 0) >= 0 ? "var(--green)" : "var(--red)" }}>
        {c.pnl != null ? money(c.pnl) : "—"}
      </td>
    </tr>
  );
}

export default function EnginePage() {
  const [li, setLi] = useState<LiveIntent | null>(null);
  useEffect(() => {
    let on = true;
    const tick = () => getLiveIntent().then((r) => on && setLi(r)).catch(() => {});
    tick();
    const id = setInterval(tick, 6000);
    return () => { on = false; clearInterval(id); };
  }, []);

  if (!li) return <div className="p-6 text-[13px]" style={{ color: "var(--muted)" }}>loading engine…</div>;
  if (!li.available) {
    return (
      <div className="p-6 text-[13px]" style={{ color: "var(--muted)" }}>
        <div className="text-[15px] font-bold mb-2" style={{ color: "var(--text)" }}>Live engine not attached</div>
        {li.reason}
      </div>
    );
  }

  const cands = li.candidates ?? [];
  const watching = cands.filter((c) => c.state === "watch").length;
  const holding = cands.filter((c) => c.state === "holding").length;
  const done = cands.filter((c) => c.state === "done").length;

  return (
    <div className="h-full flex flex-col">
      {/* header strip */}
      <div className="flex items-center gap-3 px-4 h-12 shrink-0 flex-wrap" style={{ background: "var(--panel)", borderBottom: "1px solid var(--line)" }}>
        <span className="font-bold text-[14px]">{li.strategy}</span>
        <span className="mono text-[11px]" style={{ color: "var(--muted)" }}>{li.session} · {li.exit_policy}</span>
        {li.armed
          ? <Pill color="var(--red)">● ARMED · real paper orders</Pill>
          : <Pill color="var(--blue, #5b8def)">dry-run · nothing transmitted</Pill>}
        {li.entries_halted && <Pill color="var(--amber)">entries halted (daily stop)</Pill>}
        <div className="ml-auto flex items-center gap-4 mono text-[12px]" style={{ color: "var(--muted)" }}>
          <span>watch <b style={{ color: "var(--text)" }}>{watching}</b></span>
          <span>hold <b style={{ color: "var(--green)" }}>{holding}</b></span>
          <span>done <b style={{ color: "var(--text)" }}>{done}</b></span>
          <span>day P&L <b style={{ color: (li.day_pnl ?? 0) >= 0 ? "var(--green)" : "var(--red)" }}>{money(li.day_pnl ?? 0)}</b></span>
          {li.transmitted_count != null && <span>sent <b style={{ color: "var(--text)" }}>{li.transmitted_count}</b></span>}
        </div>
      </div>

      <div className="grow min-h-0 overflow-auto">
        {cands.length === 0 ? (
          <div className="p-6 text-[13px]" style={{ color: "var(--muted)" }}>
            No candidates yet. The engine scans the full market each poll and watches every gapper
            in the price band; names appear here as they qualify (and during the session window).
          </div>
        ) : (
          <table className="w-full text-[12px]" style={{ borderCollapse: "collapse" }}>
            <thead className="sticky top-0" style={{ background: "var(--panel)" }}>
              <tr className="text-left" style={{ color: "var(--muted)", borderBottom: "1px solid var(--line)" }}>
                <th className="px-3 py-2">symbol</th><th className="px-3 py-2">state</th>
                <th className="px-3 py-2 text-right">gap</th><th className="px-3 py-2 text-right">last</th>
                <th className="px-3 py-2 text-right">trigger (HOD)</th><th className="px-3 py-2 text-right">vs trig</th>
                <th className="px-3 py-2 text-right">bars</th><th className="px-3 py-2 text-right">entry</th>
                <th className="px-3 py-2 text-right">stop</th><th className="px-3 py-2 text-right">exit</th>
                <th className="px-3 py-2 text-right">P&L</th>
              </tr>
            </thead>
            <tbody>{cands.map((c) => <CandidateRow key={c.symbol} c={c} />)}</tbody>
          </table>
        )}
      </div>

      <div className="shrink-0 px-4 py-1.5 text-[10px] mono" style={{ color: "var(--muted)", borderTop: "1px solid var(--line)" }}>
        "trigger" = high-of-session so far (the level a breakout must clear). Reconciled engine — these are exactly the Lab's entries/exits.
      </div>
    </div>
  );
}
