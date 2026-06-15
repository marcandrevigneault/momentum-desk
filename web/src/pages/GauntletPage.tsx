import { useEffect, useState } from "react";
import { getGauntlet } from "../api";
import type { Gauntlet, GauntletSession } from "../types";

/** The evaluation gauntlet: does the candidate edge survive honest scrutiny?
 *  Phase 3 of the edge-detection platform (docs/EDGE_PLATFORM.md). */

const STATUS = {
  pass: { c: "var(--green)", mark: "✓" },
  caution: { c: "var(--amber)", mark: "~" },
  fail: { c: "var(--red)", mark: "✗" },
} as const;

const verdictColor = (v: string) =>
  v.startsWith("SURVIVES") ? "var(--green)" : v.startsWith("FRAGILE") ? "var(--amber)" : "var(--red)";
const rColor = (r: number) => (r >= 0 ? "var(--green)" : "var(--red)");
const fmt = (v: number, d = 3) => (v >= 0 ? "+" : "") + v.toFixed(d);

function Stat({ label, value, color, title }: { label: string; value: string; color?: string; title?: string }) {
  return (
    <div className="flex flex-col" title={title}>
      <span className="text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>{label}</span>
      <span className="mono text-[14px] font-bold" style={{ color: color ?? "var(--text)" }}>{value}</span>
    </div>
  );
}

function SessionCard({ s }: { s: GauntletSession }) {
  return (
    <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--line)" }}>
      <div className="px-4 py-3 flex items-center gap-3 flex-wrap" style={{ background: "var(--panel)", borderBottom: "1px solid var(--line)" }}>
        <div className="font-bold text-[14px] capitalize">{s.session}</div>
        <span className="mono text-[11px] px-1.5 rounded" style={{ border: "1px solid var(--line)", color: "var(--muted)" }}>{s.candidate}</span>
        <span className="mono text-[11px]" style={{ color: "var(--muted)" }}>{s.n_trades} trades · {s.n_days} days</span>
      </div>

      {/* verdict banner */}
      <div className="px-4 py-2.5 text-[13px] font-bold" style={{ background: "rgba(255,255,255,.02)", color: verdictColor(s.verdict), borderBottom: "1px solid var(--line)" }}>
        {s.verdict}
      </div>

      {/* headline stats */}
      <div className="px-4 py-3 grid grid-cols-3 gap-3" style={{ borderBottom: "1px solid var(--line)" }}>
        <Stat label="expectancy" value={`${fmt(s.expectancy_r)}R`} color={rColor(s.expectancy_r)} />
        <Stat label="daily Sharpe" value={fmt(s.sharpe_daily, 2)} title="Sharpe of daily aggregated R" />
        <Stat label="P(edge>0)" value={`${(s.boot_p_pos * 100).toFixed(0)}%`} color={s.boot_p_pos >= 0.95 ? "var(--green)" : "var(--amber)"} />
        <Stat label="boot 95% CI" value={`[${fmt(s.boot_lo, 2)}, ${fmt(s.boot_hi, 2)}]`} title="block-bootstrap CI on expectancy (R)" />
        <Stat label="DSR" value={`${(s.dsr * 100).toFixed(0)}%`} color={s.dsr >= 0.95 ? "var(--green)" : s.dsr >= 0.8 ? "var(--amber)" : "var(--red)"}
          title={`Deflated Sharpe vs SR* ${s.sr_star.toFixed(3)} over ${s.n_trials} trials`} />
        <Stat label="walk-fwd OOS" value={`${fmt(s.wf_oos_exp)}R`} color={rColor(s.wf_oos_exp)}
          title={`${s.wf_pos_folds}/${s.folds.length} folds positive`} />
      </div>

      {/* checks */}
      <div className="px-4 py-2" style={{ borderBottom: "1px solid var(--line)" }}>
        {s.checks.map((c) => (
          <div key={c.name} className="flex items-start gap-2 py-1 text-[12px]">
            <span className="mono font-bold" style={{ color: STATUS[c.status as keyof typeof STATUS].c }}>{STATUS[c.status as keyof typeof STATUS].mark}</span>
            <span className="font-semibold w-32 shrink-0">{c.name}</span>
            <span style={{ color: "var(--muted)" }}>{c.detail}</span>
          </div>
        ))}
      </div>

      {/* walk-forward folds */}
      <div className="px-4 py-3" style={{ borderBottom: "1px solid var(--line)" }}>
        <div className="text-[10px] uppercase tracking-wider mb-1.5" style={{ color: "var(--muted)" }}>Walk-forward (select best exit in-sample → measure out-of-sample)</div>
        <div className="flex gap-2 flex-wrap">
          {s.folds.map((f) => (
            <div key={f.fold} className="rounded-md px-2 py-1 mono text-[11px]" style={{ border: "1px solid var(--line)" }} title={`selected ${f.selected}; IS ${fmt(f.is_exp)} → OOS ${fmt(f.oos_exp)}`}>
              <span style={{ color: "var(--muted)" }}>f{f.fold} </span>
              <span style={{ color: rColor(f.oos_exp) }}>{fmt(f.oos_exp, 2)}R</span>
            </div>
          ))}
        </div>
      </div>

      {/* regime strip */}
      <div className="px-4 py-3">
        <div className="text-[10px] uppercase tracking-wider mb-1.5" style={{ color: "var(--muted)" }}>
          Regime — {(s.months_pos_frac * 100).toFixed(0)}% of months positive
        </div>
        <div className="flex items-end gap-[3px] h-10">
          {s.regime.map((m) => {
            const max = Math.max(0.2, ...s.regime.map((x) => Math.abs(x.expectancy_r)));
            return (
              <div key={m.period} className="flex-1 flex flex-col justify-center" style={{ minWidth: 8 }} title={`${m.period}: ${fmt(m.expectancy_r)}R (${m.n} trades)`}>
                <div style={{ height: `${(Math.abs(m.expectancy_r) / max) * 50}%`, background: rColor(m.expectancy_r), alignSelf: "stretch", marginTop: m.expectancy_r >= 0 ? "auto" : 0, marginBottom: m.expectancy_r >= 0 ? "50%" : "auto" }} />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export default function GauntletPage() {
  const [g, setG] = useState<Gauntlet | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    getGauntlet().then(setG).catch(() => setErr(true));
  }, []);

  if (err) return <div className="p-6 text-[13px]" style={{ color: "var(--red)" }}>Failed to load gauntlet.</div>;
  if (!g) return <div className="p-6 text-[13px]" style={{ color: "var(--muted)" }}>Loading gauntlet…</div>;

  const sessions = Object.values(g.sessions);
  return (
    <div className="h-full overflow-auto p-5">
      <div className="flex items-center gap-3 mb-1">
        <h2 className="text-[18px] font-bold m-0">Evaluation gauntlet</h2>
        <span className="badge" style={{ color: "var(--muted)", borderColor: "var(--line)" }}>{g.source}</span>
      </div>
      <p className="text-[12px] mb-4 max-w-3xl" style={{ color: "var(--muted)" }}>
        Does the candidate strategy survive honest scrutiny ({g.days ?? "?"} days of {g.data ?? "real"} data)?
        Every prior phase <i>searched</i>, and searching inflates whatever looks best. Five independent checks
        try to kill it: a block-bootstrap CI on expectancy, a <b>deflated Sharpe</b> (corrects for how many
        configs we tried), purged walk-forward with in-sample selection, per-month regime stability, and an
        untouched holdout. A claim that clears all five is hard to fake.
      </p>
      <div className="grid gap-5" style={{ gridTemplateColumns: sessions.length > 1 ? "1fr 1fr" : "1fr" }}>
        {sessions.map((s) => <SessionCard key={s.session} s={s} />)}
      </div>
      <p className="text-[11px] mt-4 mono" style={{ color: "var(--muted)" }}>
        DSR ≥ 95% = significant after multiple-testing correction. The deflation is what stops a backtest from
        lying to you. Not advice.
      </p>
    </div>
  );
}
