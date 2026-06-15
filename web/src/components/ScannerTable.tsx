import type { Signal } from "../types";

const FLAG_STYLE: Record<string, { label: string; bg: string; fg: string }> = {
  extended_above_vwap: { label: "DON'T CHASE", bg: "rgba(248,113,113,.15)", fg: "var(--red)" },
  you_would_be_the_liquidity: { label: "YOU'D BE LIQUIDITY", bg: "rgba(248,113,113,.15)", fg: "var(--red)" },
  halted: { label: "HALTED", bg: "rgba(251,191,36,.15)", fg: "var(--amber)" },
  no_news_catalyst: { label: "NO CATALYST", bg: "rgba(139,148,158,.15)", fg: "var(--muted)" },
  unknown_float: { label: "FLOAT?", bg: "rgba(139,148,158,.15)", fg: "var(--muted)" },
};

function Flag({ flag }: { flag: string }) {
  const s = FLAG_STYLE[flag] ?? { label: flag, bg: "rgba(139,148,158,.15)", fg: "var(--muted)" };
  return <span className="flag" style={{ background: s.bg, color: s.fg }}>{s.label}</span>;
}

const num = (v: number, d = 2) => v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const TH = "text-left font-semibold text-[11px] uppercase tracking-wider px-3 py-2";
const tdBase = "px-3 py-2 align-middle";

export default function ScannerTable({
  signals, selected, onSelect,
}: {
  signals: Signal[];
  selected: string | null;
  onSelect: (s: string) => void;
}) {
  if (signals.length === 0) {
    return (
      <div className="grid place-items-center h-full text-[13px]" style={{ color: "var(--muted)" }}>
        No candidates in the scan band right now.
      </div>
    );
  }
  return (
    <div className="overflow-auto h-full">
      <table className="w-full border-collapse">
        <thead className="sticky top-0 z-10" style={{ background: "var(--panel)" }}>
          <tr style={{ color: "var(--muted)", borderBottom: "1px solid var(--line)" }}>
            <th className={TH}>Sym</th>
            <th className={`${TH} text-right`}>Last</th>
            <th className={`${TH} text-right`}>Gap %</th>
            <th className={`${TH} text-right`}>RVOL</th>
            <th className={`${TH} text-right`}>+VWAP</th>
            <th className={`${TH} text-right`}>Score</th>
            <th className={TH}>Catalyst / flags</th>
          </tr>
        </thead>
        <tbody className="mono">
          {signals.map((s) => {
            const ext = s.extension_above_vwap_pct;
            const isSel = selected === s.symbol;
            return (
              <tr
                key={s.symbol}
                onClick={() => onSelect(s.symbol)}
                className="cursor-pointer"
                style={{
                  borderBottom: "1px solid var(--line)",
                  opacity: s.actionable ? 1 : 0.62,
                  background: isSel ? "var(--panel-2)" : undefined,
                  boxShadow: isSel ? "inset 2px 0 0 var(--blue)" : undefined,
                }}
              >
                <td className={`${tdBase} font-bold`} style={{ fontFamily: "Inter" }}>
                  <span style={{ color: s.actionable ? "var(--green)" : "var(--muted)" }}>{s.actionable ? "●" : "○"}</span>{" "}
                  {s.symbol}
                  {s.held && <span className="flag ml-1.5" style={{ background: "rgba(96,165,250,.15)", color: "var(--blue)" }}>HELD</span>}
                </td>
                <td className={`${tdBase} text-right`}>${num(s.last)}</td>
                <td className={`${tdBase} text-right`} style={{ color: "var(--green)" }}>+{num(s.gap_pct, 1)}</td>
                <td className={`${tdBase} text-right`}>{num(s.relative_volume, 1)}×</td>
                <td className={`${tdBase} text-right`} style={{ color: ext > 8 ? "var(--red)" : ext > 4 ? "var(--amber)" : "var(--muted)" }}>
                  +{num(ext, 1)}%
                </td>
                <td className={`${tdBase} text-right font-bold`} style={{ color: "var(--blue)" }}>{num(s.score, 1)}</td>
                <td className={tdBase}>
                  <div className="flex flex-wrap items-center gap-1.5">
                    {s.has_news && (
                      <span className="truncate max-w-[220px] text-[12px]" style={{ fontFamily: "Inter", color: "var(--text)" }} title={s.news_headline}>
                        {s.news_headline || "news"}
                      </span>
                    )}
                    {s.flags.map((f) => <Flag key={f} flag={f} />)}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
