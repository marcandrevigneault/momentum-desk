import { useEffect, useState } from "react";
import { getRules } from "../api";
import type { RulesSnapshot } from "../types";

/** AND/OR rule combos (#4): compose entry conditions with AND/OR + an exit
 *  policy, and compare them head-to-head. Shows how tightening (AND) trades
 *  quantity for quality vs loosening (OR). */

const rColor = (v: number) => (v >= 0 ? "var(--green)" : "var(--red)");

export default function RulesPage() {
  const [snap, setSnap] = useState<RulesSnapshot | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => { getRules().then(setSnap).catch(() => setErr(true)); }, []);

  if (err) return <div className="p-6 text-[13px]" style={{ color: "var(--red)" }}>Failed to load rules.</div>;
  if (!snap) return <div className="p-6 text-[13px]" style={{ color: "var(--muted)" }}>Loading rules…</div>;
  if (!snap.results?.length) return <div className="p-6 text-[13px]" style={{ color: "var(--muted)" }}>No rule combos run yet.</div>;

  const best = Math.max(...snap.results.map((r) => r.daily_sharpe));
  return (
    <div className="h-full overflow-auto p-5">
      <div className="flex items-center gap-3 mb-1">
        <h2 className="text-[18px] font-bold m-0">Entry/exit rule combos (AND / OR)</h2>
        <span className="badge" style={{ color: "var(--muted)", borderColor: "var(--line)" }}>{snap.source}</span>
        <span className="mono text-[11px]" style={{ color: "var(--muted)" }}>{snap.session} · {snap.days} days</span>
      </div>
      <p className="text-[12px] mb-4 max-w-3xl" style={{ color: "var(--muted)" }}>
        Each row is an entry rule — feature conditions combined with <b>AND</b> (all must hold) or <b>OR</b>
        (any) — paired with an exit policy, run over the same breakout events. AND tightens to fewer,
        higher-quality trades; OR loosens to more, weaker ones. Ranked by daily Sharpe.
      </p>
      <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--line)" }}>
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)", background: "var(--panel)" }}>
              <th className="text-left px-4 py-2">Rule</th>
              <th className="text-left">Logic</th>
              <th className="text-left">Exit</th>
              <th className="text-right">trades</th>
              <th className="text-right">exp R</th>
              <th className="text-right">win%</th>
              <th className="text-right">PF</th>
              <th className="text-right px-3">Sharpe</th>
            </tr>
          </thead>
          <tbody>
            {snap.results.map((r) => (
              <tr key={r.name} style={{ borderTop: "1px solid var(--line)", background: r.daily_sharpe === best ? "rgba(52,211,153,.07)" : undefined }}>
                <td className="px-4 py-2 font-semibold">{r.name}{r.daily_sharpe === best && <span className="ml-2 text-[9px] px-1 rounded" style={{ background: "var(--green)", color: "#04110b" }}>best</span>}</td>
                <td className="mono text-[11px]" style={{ color: "var(--muted)" }}>{r.rule}</td>
                <td className="mono text-[11px]" style={{ color: "var(--muted)" }}>{r.exit_policy}</td>
                <td className="text-right mono" style={{ color: "var(--muted)" }}>{r.n}</td>
                <td className="text-right mono" style={{ color: rColor(r.expectancy_r) }}>{r.expectancy_r >= 0 ? "+" : ""}{r.expectancy_r.toFixed(3)}</td>
                <td className="text-right mono">{(r.win_rate * 100).toFixed(1)}%</td>
                <td className="text-right mono">{r.profit_factor > 1e6 ? "∞" : r.profit_factor.toFixed(2)}</td>
                <td className="text-right mono px-3 font-bold">{r.daily_sharpe >= 0 ? "+" : ""}{r.daily_sharpe.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] mt-4 mono" style={{ color: "var(--muted)" }}>
        Finding: "low-ext AND rvol-cap" (strict) gives the best quality (PF/win); OR loosens it. RVOL-cap is the
        dominant filter — consistent with the edge screen. In-sample; magnitudes are upper bounds. Not advice.
      </p>
    </div>
  );
}
