import { useEffect, useState } from "react";
import { getActiveStrategy, getCombos, getCombosOptimize, getGauntlet, getOptimize, setActiveStrategy } from "../api";
import type { ActiveStrategy, CombosSnapshot, Gauntlet, OptimizeSnapshot } from "../types";

/** Strategy analyser — one place to interpret and COMPARE everything built, and
 *  pick an "active strategy". Optimized models are flagged (✓ robust / ✗ overfit
 *  after deflation). Each row shows the model's variables so the model is legible. */

const rColor = (v: number) => (v >= 0 ? "var(--green)" : "var(--red)");
const fmt = (v: number | null | undefined, d = 2) => (v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(d));

interface Row {
  id: string;
  name: string;
  kind: string;
  vars: string;
  expectancy_r: number | null;
  pf: number | null;
  sharpe: number | null;
  verdict: string;      // gauntlet verdict or "—"
  optimized: "yes" | "overfit" | "na";
}

function OptBadge({ o }: { o: Row["optimized"] }) {
  if (o === "yes") return <span className="text-[10px] px-1.5 rounded" style={{ background: "var(--green)", color: "#04110b" }}>✓ optimized</span>;
  if (o === "overfit") return <span className="text-[10px] px-1.5 rounded" style={{ background: "var(--red)", color: "#fff" }}>✗ overfit</span>;
  return <span className="text-[10px] px-1.5 rounded" style={{ border: "1px solid var(--line)", color: "var(--muted)" }}>not optimized</span>;
}

export default function AnalyserPage() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [active, setActive] = useState<ActiveStrategy>({ active: null });
  const [busy, setBusy] = useState(false);

  async function load() {
    const [g, c, o, a, co] = await Promise.all([getGauntlet(), getCombos(), getOptimize(), getActiveStrategy(), getCombosOptimize()]) as
      [Gauntlet, CombosSnapshot, OptimizeSnapshot, ActiveStrategy, Awaited<ReturnType<typeof getCombosOptimize>>];
    const out: Row[] = [];
    // single-session strategies from the gauntlet + optimizer
    for (const s of ["intraday", "premarket"]) {
      const gs = g.sessions?.[s];
      const os = o.sessions?.[s];
      if (!gs && !os) continue;
      out.push({
        id: `strategy:${s}`,
        name: `${s[0].toUpperCase()}${s.slice(1)} momentum`,
        kind: "strategy",
        vars: os?.best_label ?? "pct_trail_10 · low-ext · RVOL-capped",
        expectancy_r: gs?.expectancy_r ?? null,
        pf: null,
        sharpe: gs?.sharpe_daily ?? os?.best_sharpe ?? null,
        verdict: gs?.verdict?.split(" —")[0] ?? "—",
        optimized: os ? (os.robust ? "yes" : "overfit") : "na",
      });
    }
    // combos (now parameter-optimized — best config from the combo sweep)
    const bbc = co.best_by_combo ?? {};
    for (const [k, cb] of Object.entries(c.combos ?? {})) {
      const m = cb.metrics;
      const opt = bbc[k];
      out.push({
        id: `combo:${k}`, name: cb.label ?? k, kind: "combo",
        vars: cb.legs.join(" + ") + (opt ? ` · best: ${opt.intraday_exit}·mc${opt.max_concurrent}` : ""),
        expectancy_r: m?.expectancy_r ?? null, pf: m?.profit_factor ?? null,
        sharpe: opt?.daily_sharpe ?? null, verdict: "—",
        optimized: opt ? "yes" : "na",
      });
    }
    setRows(out);
    setActive(a);
  }

  useEffect(() => { load().catch(() => setRows([])); }, []);

  async function choose(r: Row) {
    setBusy(true);
    const res = await setActiveStrategy(r.id, r.name);
    setActive(res);
    setBusy(false);
  }

  if (!rows) return <div className="p-6 text-[13px]" style={{ color: "var(--muted)" }}>Loading analyser…</div>;

  return (
    <div className="h-full overflow-auto p-5">
      <h2 className="text-[18px] font-bold mb-1">Strategy analyser</h2>
      <p className="text-[12px] mb-3 max-w-3xl" style={{ color: "var(--muted)" }}>
        Every model and combo in one place — its variables, edge metrics, gauntlet verdict, and whether it's been
        optimized (✓ = its best config survives the deflated-Sharpe multiple-testing bar; ✗ = the search overfit).
        Pick one as the <b>active strategy</b>; that's what the live loop should trade.
      </p>
      {active.active && (
        <div className="rounded-lg px-4 py-2 mb-4 text-[13px]" style={{ background: "rgba(52,211,153,.08)", border: "1px solid var(--green)" }}>
          ★ Active strategy: <b>{active.label || active.active}</b>
        </div>
      )}
      <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--line)" }}>
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)", background: "var(--panel)" }}>
              <th className="text-left px-4 py-2">Strategy</th>
              <th className="text-left">Variables</th>
              <th className="text-right">exp R</th>
              <th className="text-right">PF</th>
              <th className="text-right">Sharpe</th>
              <th className="text-left px-2">Gauntlet</th>
              <th className="text-left px-2">Optimized</th>
              <th className="text-right px-3">Active</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const isActive = active.active === r.id;
              return (
                <tr key={r.id} style={{ borderTop: "1px solid var(--line)", background: isActive ? "rgba(52,211,153,.07)" : undefined }}>
                  <td className="px-4 py-2">
                    <span className="font-semibold">{r.name}</span>
                    <span className="ml-2 text-[9px] px-1 rounded" style={{ border: "1px solid var(--line)", color: "var(--muted)" }}>{r.kind}</span>
                  </td>
                  <td className="mono text-[11px]" style={{ color: "var(--muted)" }}>{r.vars}</td>
                  <td className="text-right mono" style={{ color: r.expectancy_r != null ? rColor(r.expectancy_r) : "var(--muted)" }}>{fmt(r.expectancy_r, 3)}{r.expectancy_r != null ? "R" : ""}</td>
                  <td className="text-right mono">{r.pf != null ? r.pf.toFixed(2) : "—"}</td>
                  <td className="text-right mono">{fmt(r.sharpe)}</td>
                  <td className="px-2 text-[11px]" style={{ color: r.verdict.startsWith("SURVIVES") ? "var(--green)" : "var(--muted)" }}>{r.verdict}</td>
                  <td className="px-2"><OptBadge o={r.optimized} /></td>
                  <td className="text-right px-3">
                    <button disabled={busy || isActive} onClick={() => choose(r)} className="text-[11px] px-2 py-1 rounded"
                      style={{ border: "1px solid var(--line)", color: isActive ? "var(--green)" : "var(--text)" }}>
                      {isActive ? "★ active" : "set active"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] mt-4 mono" style={{ color: "var(--muted)" }}>
        Read it: intraday momentum is the robust, optimized edge; premarket's best config overfit the search;
        combos don't beat intraday-alone (the fade drags). All magnitudes are upper bounds. Not advice.
      </p>
    </div>
  );
}
