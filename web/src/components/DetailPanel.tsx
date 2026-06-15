import { useState } from "react";
import { closeTrade, openTrade } from "../api";
import type { Point, Position, Signal } from "../types";
import CandidateChart from "./CandidateChart";

function Cond({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>{label}</span>
      <span className="mono text-[13px] font-bold" style={{ color }}>{value}</span>
    </div>
  );
}

export default function DetailPanel({
  signal, position, points,
}: {
  signal: Signal | null;
  position: Position | null;
  points: Point[];
}) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  if (!signal && !position) {
    return (
      <div className="grid place-items-center h-full text-[13px]" style={{ color: "var(--muted)" }}>
        Select a candidate to see its chart and trade plan.
      </div>
    );
  }
  const sym = signal?.symbol ?? position!.symbol;
  const plan = signal?.plan;
  const entry = position?.entry ?? plan?.entry;
  const stop = position?.stop ?? plan?.stop;
  const target = position?.target ?? plan?.target;
  const trailStop = position?.stop;   // live trailing level once held

  const act = async (fn: () => Promise<{ ok: boolean; reasons?: string[]; pnl?: number }>) => {
    setBusy(true);
    setMsg(null);
    const r = await fn();
    setMsg(r.ok ? (r.pnl != null ? `closed · P&L $${r.pnl.toFixed(2)}` : "position opened") : (r.reasons?.join("; ") ?? "rejected"));
    setBusy(false);
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-4 px-3 py-2 shrink-0" style={{ borderBottom: "1px solid var(--line)" }}>
        <div className="font-bold text-[15px]">{sym}</div>
        {plan && <Cond label="entry" value={`$${entry?.toFixed(2)}`} color="var(--blue)" />}
        {stop != null && <Cond label={position ? "trail stop" : "stop"} value={`$${stop.toFixed(2)}`} color="var(--red)" />}
        {target != null && <Cond label="target" value={`$${target.toFixed(2)}`} color="var(--green)" />}
        {plan && <Cond label="size" value={`${plan.shares} sh`} color="var(--text)" />}
        {position && <Cond label="unreal P&L" value={`$${position.unrealized_pnl.toFixed(2)}`}
          color={position.unrealized_pnl >= 0 ? "var(--green)" : "var(--red)"} />}

        <div className="ml-auto flex items-center gap-2">
          {msg && <span className="mono text-[11px]" style={{ color: "var(--muted)" }}>{msg}</span>}
          {position ? (
            <button className="btn btn-sell" disabled={busy} onClick={() => act(() => closeTrade(sym))}>
              Close
            </button>
          ) : (
            <button
              className="btn btn-buy"
              disabled={busy || !signal?.actionable || !plan?.ok}
              title={signal?.actionable ? "" : "flagged — not actionable"}
              onClick={() => act(() => openTrade(sym))}
            >
              Paper buy
            </button>
          )}
        </div>
      </div>
      <div className="grow min-h-0 p-2">
        <CandidateChart points={points} entry={entry} stop={stop} target={target} trailStop={trailStop} />
      </div>
    </div>
  );
}
