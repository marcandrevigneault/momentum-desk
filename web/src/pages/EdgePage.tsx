import { useEffect, useState } from "react";
import { getEdge } from "../api";
import type { EdgeFeature, EdgeScreen, EdgeSessionScreen } from "../types";

/** Edge findings: per-feature information coefficient + decile-lift for each
 *  session — the readable answer to "which variables carry edge?". Phase 1 of
 *  the edge-detection platform (see docs/EDGE_PLATFORM.md). */

const icColor = (ic: number) => {
  const m = Math.min(1, Math.abs(ic) / 0.4); // saturate around |IC|=0.4
  return ic >= 0 ? `rgba(52,211,153,${0.25 + 0.75 * m})` : `rgba(248,113,113,${0.25 + 0.75 * m})`;
};
const rColor = (r: number) => (r >= 0 ? "var(--green)" : "var(--red)");
const fmt = (v: number, d = 2) => (v >= 0 ? "+" : "") + v.toFixed(d);

/** A tiny inline decile sparkline: one bar per decile, green up / red down,
 *  height ∝ |mean forward R|. Lets you eyeball monotonicity at a glance. */
function DecileSpark({ f }: { f: EdgeFeature }) {
  const max = Math.max(0.2, ...f.deciles.map((d) => Math.abs(d.mean_fwd_r)));
  return (
    <div className="flex items-end gap-[2px] h-7" title="mean forward-R per decile (low → high feature value)">
      {f.deciles.map((d, i) => (
        <div key={i} className="relative w-[7px] flex flex-col justify-center" style={{ height: "100%" }}>
          <div
            style={{
              height: `${(Math.abs(d.mean_fwd_r) / max) * 50}%`,
              background: rColor(d.mean_fwd_r),
              marginTop: d.mean_fwd_r >= 0 ? "auto" : 0,
              marginBottom: d.mean_fwd_r >= 0 ? "50%" : "auto",
              alignSelf: d.mean_fwd_r >= 0 ? "flex-end" : "flex-start",
            }}
          />
        </div>
      ))}
    </div>
  );
}

function SessionCard({ s }: { s: EdgeSessionScreen }) {
  return (
    <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--line)" }}>
      <div className="px-4 py-3 flex items-center gap-4" style={{ background: "var(--panel)", borderBottom: "1px solid var(--line)" }}>
        <div className="font-bold text-[14px] capitalize">{s.session}</div>
        <span className="mono text-[11px]" style={{ color: "var(--muted)" }}>{s.n_events.toLocaleString()} events</span>
        <span className="mono text-[11px]" style={{ color: "var(--muted)" }}>baseline <b style={{ color: rColor(s.baseline_fwd_r) }}>{fmt(s.baseline_fwd_r)}R</b></span>
        <span className="mono text-[11px]" style={{ color: "var(--muted)" }}>win <b style={{ color: "var(--text)" }}>{(s.win_rate * 100).toFixed(1)}%</b></span>
      </div>
      <table className="w-full text-[12px]">
        <thead>
          <tr className="text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>
            <th className="text-left px-4 py-2">Feature</th>
            <th className="text-right px-2">IC</th>
            <th className="text-right px-2">bot R</th>
            <th className="text-right px-2">top R</th>
            <th className="text-left px-3">deciles (low→high)</th>
          </tr>
        </thead>
        <tbody>
          {s.features.map((f) => {
            const bot = f.deciles[0]?.mean_fwd_r ?? 0;
            const top = f.deciles[f.deciles.length - 1]?.mean_fwd_r ?? 0;
            return (
              <tr key={f.name} style={{ borderTop: "1px solid var(--line)" }} title={f.desc}>
                <td className="px-4 py-2">
                  <div className="flex items-center gap-2">
                    <span className="mono font-semibold">{f.name}</span>
                    <span className="text-[9px] px-1 rounded" style={{ border: "1px solid var(--line)", color: "var(--muted)" }}>{f.kind}</span>
                  </div>
                </td>
                <td className="text-right px-2 mono font-bold" style={{ background: icColor(f.ic) }}>{fmt(f.ic, 2)}</td>
                <td className="text-right px-2 mono" style={{ color: rColor(bot) }}>{fmt(bot)}</td>
                <td className="text-right px-2 mono" style={{ color: rColor(top) }}>{fmt(top)}</td>
                <td className="px-3 py-1"><DecileSpark f={f} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function EdgePage() {
  const [edge, setEdge] = useState<EdgeScreen | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    getEdge().then(setEdge).catch(() => setErr(true));
  }, []);

  if (err) return <div className="p-6 text-[13px]" style={{ color: "var(--red)" }}>Failed to load edge screen.</div>;
  if (!edge) return <div className="p-6 text-[13px]" style={{ color: "var(--muted)" }}>Loading edge screen…</div>;

  const sessions = Object.values(edge.sessions);
  return (
    <div className="h-full overflow-auto p-5">
      <div className="flex items-center gap-3 mb-1">
        <h2 className="text-[18px] font-bold m-0">Edge findings</h2>
        <span className="badge" style={{ color: "var(--muted)", borderColor: "var(--line)" }}>
          {edge.source === "live" ? "live" : "snapshot"}
        </span>
      </div>
      <p className="text-[12px] mb-4 max-w-3xl" style={{ color: "var(--muted)" }}>
        Univariate screen over {edge.days ?? "?"} days of {edge.data ?? "real"} data
        {edge.generated ? ` (generated ${edge.generated})` : ""}. For each variable: the Spearman
        information coefficient (rank correlation with forward R, green = higher value → better,
        red = worse) and the mean forward R in its bottom vs top decile. The entry trigger and exit
        are held fixed, so this measures <i>entry quality</i>. Univariate only — correlated features
        can't be summed; multivariate and significance testing come in later phases.
      </p>
      <div className="grid gap-5" style={{ gridTemplateColumns: sessions.length > 1 ? "1fr 1fr" : "1fr" }}>
        {sessions.map((s) => <SessionCard key={s.session} s={s} />)}
      </div>
      <p className="text-[11px] mt-4 mono" style={{ color: "var(--muted)" }}>
        Reading it: a strongly negative IC (e.g. move_from_open, rvol) means the most-extended /
        highest-RVOL entries do <b>worse</b> — chasing is the enemy. Not advice.
      </p>
    </div>
  );
}
