import { useEffect, useState } from "react";
import { getExitLab } from "../api";
import type { ExitLab, ExitLabSession, ExitMetrics } from "../types";

/** Exit-policy lab: same entries, different exits, compared head-to-head per
 *  session. Phase 2 of the edge-detection platform (docs/EDGE_PLATFORM.md). */

const rColor = (r: number) => (r >= 0 ? "var(--green)" : "var(--red)");
const fmt = (v: number, d = 2) => (v >= 0 ? "+" : "") + v.toFixed(d);

/** Risk-adjusted score = expectancy per unit of max drawdown. The lab's real
 *  point: nearly-equal expectancy with far less drawdown is the better exit. */
const radj = (m: ExitMetrics) => (m.max_dd_r > 0 ? m.expectancy_r / m.max_dd_r : 0);

function SessionCard({ s }: { s: ExitLabSession }) {
  const bestRadj = Math.max(...s.policies.map(radj));
  const maxExp = Math.max(...s.policies.map((p) => Math.abs(p.expectancy_r)));
  return (
    <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--line)" }}>
      <div className="px-4 py-3 flex items-center gap-4" style={{ background: "var(--panel)", borderBottom: "1px solid var(--line)" }}>
        <div className="font-bold text-[14px] capitalize">{s.session}</div>
        <span className="mono text-[11px]" style={{ color: "var(--muted)" }}>{s.n_events.toLocaleString()} entries</span>
      </div>
      <table className="w-full text-[12px]">
        <thead>
          <tr className="text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>
            <th className="text-left px-4 py-2">Exit policy</th>
            <th className="text-right px-2">exp R</th>
            <th className="text-right px-2">win%</th>
            <th className="text-right px-2">PF</th>
            <th className="text-right px-2">maxDD R</th>
            <th className="text-right px-2">hold</th>
            <th className="text-right px-4" title="expectancy per unit of drawdown">exp/DD</th>
          </tr>
        </thead>
        <tbody>
          {s.policies.map((m) => {
            const isBest = radj(m) === bestRadj;
            const pf = m.profit_factor > 1e6 ? "∞" : m.profit_factor.toFixed(2);
            return (
              <tr key={m.policy} style={{ borderTop: "1px solid var(--line)", background: isBest ? "rgba(52,211,153,.07)" : undefined }} title={m.desc}>
                <td className="px-4 py-2">
                  <span className="mono font-semibold">{m.policy}</span>
                  {isBest && <span className="ml-2 text-[9px] px-1 rounded" style={{ background: "var(--green)", color: "#04110b" }}>best risk-adj</span>}
                </td>
                <td className="text-right px-2">
                  <div className="flex items-center justify-end gap-1.5">
                    <span className="inline-block rounded-sm" style={{ width: `${(Math.abs(m.expectancy_r) / maxExp) * 34}px`, height: 7, background: rColor(m.expectancy_r) }} />
                    <span className="mono font-bold" style={{ color: rColor(m.expectancy_r) }}>{fmt(m.expectancy_r)}</span>
                  </div>
                </td>
                <td className="text-right px-2 mono">{(m.win_rate * 100).toFixed(1)}%</td>
                <td className="text-right px-2 mono">{pf}</td>
                <td className="text-right px-2 mono" style={{ color: "var(--amber)" }}>{m.max_dd_r.toFixed(1)}</td>
                <td className="text-right px-2 mono" style={{ color: "var(--muted)" }}>{m.avg_hold_bars.toFixed(0)}</td>
                <td className="text-right px-4 mono font-semibold">{radj(m).toFixed(3)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function ExitLabPage() {
  const [lab, setLab] = useState<ExitLab | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    getExitLab().then(setLab).catch(() => setErr(true));
  }, []);

  if (err) return <div className="p-6 text-[13px]" style={{ color: "var(--red)" }}>Failed to load exit lab.</div>;
  if (!lab) return <div className="p-6 text-[13px]" style={{ color: "var(--muted)" }}>Loading exit lab…</div>;

  const sessions = Object.values(lab.sessions);
  return (
    <div className="h-full overflow-auto p-5">
      <div className="flex items-center gap-3 mb-1">
        <h2 className="text-[18px] font-bold m-0">Exit-policy lab</h2>
        <span className="badge" style={{ color: "var(--muted)", borderColor: "var(--line)" }}>{lab.source}</span>
      </div>
      <p className="text-[12px] mb-4 max-w-3xl" style={{ color: "var(--muted)" }}>
        The same entries ({lab.days ?? "?"} days of {lab.data ?? "real"} data, {lab.slippage ?? "?"}% slippage)
        run through every exit policy, so any difference is the exit alone. R = per-share P&L ÷ per-share risk,
        filled pessimistically. The column that matters is <b>exp/DD</b> — expectancy per unit of drawdown:
        a policy that nearly matches "let it run" with far less drawdown is the better exit. In-sample on one
        regime — the deflated significance test comes in the next phase.
      </p>
      <div className="grid gap-5" style={{ gridTemplateColumns: sessions.length > 1 ? "1fr 1fr" : "1fr" }}>
        {sessions.map((s) => <SessionCard key={s.session} s={s} />)}
      </div>
      <p className="text-[11px] mt-4 mono" style={{ color: "var(--muted)" }}>
        Finding: trailing stops dominate fixed-R targets (which cap the fat-tail runners). A ~10% trail keeps
        most of "let it run" expectancy while cutting drawdown 2–4×; a tight ATR trail minimizes drawdown
        further at some expectancy cost. Holding to time has the highest mean R but a brutal tail. Not advice.
      </p>
    </div>
  );
}
