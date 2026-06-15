import type { Trade } from "../types";

const TH = "text-left font-semibold text-[10px] uppercase tracking-wider px-2.5 py-1.5";
const TD = "px-2.5 py-1.5";

const REASON = { target: "var(--green)", trail: "var(--amber)", manual: "var(--muted)" } as Record<string, string>;

export default function Trades({ trades }: { trades: Trade[] }) {
  const realized = trades.reduce((a, t) => a + t.pnl, 0);
  const fees = trades.reduce((a, t) => a + t.commission, 0);
  return (
    <div className="flex flex-col h-full">
      <div className="section-title px-3 py-1.5 shrink-0 flex justify-between">
        <span>Closed trades ({trades.length})</span>
        {trades.length > 0 && (
          <span className="mono" style={{ color: realized >= 0 ? "var(--green)" : "var(--red)" }}>
            net {realized >= 0 ? "+" : ""}${realized.toFixed(2)} · fees ${fees.toFixed(2)}
          </span>
        )}
      </div>
      <div className="overflow-auto grow">
        {trades.length === 0 ? (
          <div className="px-3 py-2 text-[12px]" style={{ color: "var(--muted)" }}>No closed trades yet.</div>
        ) : (
          <table className="w-full border-collapse mono text-[12px]">
            <thead style={{ color: "var(--muted)" }}>
              <tr style={{ borderBottom: "1px solid var(--line)" }}>
                <th className={TH}>Sym</th>
                <th className={`${TH} text-right`}>Entry</th>
                <th className={`${TH} text-right`}>Exit</th>
                <th className={`${TH}`}>Why</th>
                <th className={`${TH} text-right`}>Fees</th>
                <th className={`${TH} text-right`}>Net P&L</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--line)" }}>
                  <td className={`${TD} font-bold`} style={{ fontFamily: "Inter" }}>{t.symbol}</td>
                  <td className={`${TD} text-right`}>${t.entry.toFixed(2)}</td>
                  <td className={`${TD} text-right`}>${t.exit.toFixed(2)}</td>
                  <td className={TD} style={{ color: REASON[t.exit_reason] ?? "var(--muted)" }}>{t.exit_reason}</td>
                  <td className={`${TD} text-right`} style={{ color: "var(--muted)" }}>${t.commission.toFixed(2)}</td>
                  <td className={`${TD} text-right font-bold`} style={{ color: t.pnl >= 0 ? "var(--green)" : "var(--red)" }}>
                    {t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
