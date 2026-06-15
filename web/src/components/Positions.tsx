import type { Position } from "../types";

const TH = "text-left font-semibold text-[10px] uppercase tracking-wider px-2.5 py-1.5";
const TD = "px-2.5 py-1.5";

export default function Positions({
  positions, onSelect, selected,
}: {
  positions: Position[];
  onSelect: (s: string) => void;
  selected: string | null;
}) {
  return (
    <div className="flex flex-col h-full">
      <div className="section-title px-3 py-1.5 shrink-0">Open positions ({positions.length})</div>
      <div className="overflow-auto grow">
        {positions.length === 0 ? (
          <div className="px-3 py-2 text-[12px]" style={{ color: "var(--muted)" }}>No open positions.</div>
        ) : (
          <table className="w-full border-collapse mono text-[12px]">
            <thead style={{ color: "var(--muted)" }}>
              <tr style={{ borderBottom: "1px solid var(--line)" }}>
                <th className={TH}>Sym</th>
                <th className={`${TH} text-right`}>Qty</th>
                <th className={`${TH} text-right`}>Entry</th>
                <th className={`${TH} text-right`}>Last</th>
                <th className={`${TH} text-right`}>Trail</th>
                <th className={`${TH} text-right`}>Unreal P&L</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr
                  key={p.symbol}
                  onClick={() => onSelect(p.symbol)}
                  className="cursor-pointer"
                  style={{ borderBottom: "1px solid var(--line)", background: selected === p.symbol ? "var(--panel-2)" : undefined }}
                >
                  <td className={`${TD} font-bold`} style={{ fontFamily: "Inter" }}>{p.symbol}</td>
                  <td className={`${TD} text-right`}>{p.qty.toLocaleString()}</td>
                  <td className={`${TD} text-right`}>${p.entry.toFixed(2)}</td>
                  <td className={`${TD} text-right`}>${p.last.toFixed(2)}</td>
                  <td className={`${TD} text-right`} style={{ color: "var(--amber)" }}>${p.stop.toFixed(2)}</td>
                  <td className={`${TD} text-right font-bold`} style={{ color: p.unrealized_pnl >= 0 ? "var(--green)" : "var(--red)" }}>
                    {p.unrealized_pnl >= 0 ? "+" : ""}{p.unrealized_pnl.toFixed(2)}
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
