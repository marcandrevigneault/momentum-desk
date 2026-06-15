import { useEffect, useState } from "react";
import { closeTrade, getBars, openTrade } from "../api";
import type { Candle, Position, Signal } from "../types";
import CandleChart from "./CandleChart";

const TIMEFRAMES = ["1m", "5m", "1d"];

function Cond({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wider" style={{ color: "var(--muted)" }}>{label}</span>
      <span className="mono text-[13px] font-bold" style={{ color }}>{value}</span>
    </div>
  );
}

export default function DetailPanel({ signal, position }: { signal: Signal | null; position: Position | null }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [tf, setTf] = useState("1m");
  const [candles, setCandles] = useState<Candle[]>([]);
  const [loading, setLoading] = useState(false);

  const sym = signal?.symbol ?? position?.symbol ?? null;

  useEffect(() => {
    if (!sym) { setCandles([]); return; }
    let cancelled = false;
    setLoading(true);
    getBars(sym, tf)
      .then((cs) => { if (!cancelled) { setCandles(cs); setLoading(false); } })
      .catch(() => { if (!cancelled) { setCandles([]); setLoading(false); } });
    return () => { cancelled = true; };
  }, [sym, tf]);

  if (!signal && !position) {
    return (
      <div className="grid place-items-center h-full text-[13px]" style={{ color: "var(--muted)" }}>
        Select a candidate to see its chart and trade plan.
      </div>
    );
  }
  const plan = signal?.plan;
  const entry = position?.entry ?? plan?.entry;
  const stop = position?.stop ?? plan?.stop;
  const target = position?.target ?? plan?.target;
  const trailStop = position?.stop;

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
        {plan && entry != null && <Cond label="entry" value={`$${entry.toFixed(2)}`} color="var(--blue)" />}
        {stop != null && <Cond label={position ? "trail stop" : "stop"} value={`$${stop.toFixed(2)}`} color="var(--red)" />}
        {target != null && <Cond label="target" value={`$${target.toFixed(2)}`} color="var(--green)" />}
        {position && <Cond label="unreal P&L" value={`$${position.unrealized_pnl.toFixed(2)}`}
          color={position.unrealized_pnl >= 0 ? "var(--green)" : "var(--red)"} />}

        <div className="ml-auto flex items-center gap-2">
          {/* timeframe selector */}
          <div className="flex rounded-md overflow-hidden" style={{ border: "1px solid var(--line)" }}>
            {TIMEFRAMES.map((t) => (
              <button key={t} onClick={() => setTf(t)} className="mono text-[11px] px-2 py-1"
                style={{ background: tf === t ? "var(--panel-2)" : "transparent", color: tf === t ? "var(--text)" : "var(--muted)" }}>
                {t}
              </button>
            ))}
          </div>
          {loading && <span className="mono text-[10px]" style={{ color: "var(--muted)" }}>loading…</span>}
          {msg && <span className="mono text-[11px]" style={{ color: "var(--muted)" }}>{msg}</span>}
          {position ? (
            <button className="btn btn-sell" disabled={busy} onClick={() => act(() => closeTrade(sym!))}>Close</button>
          ) : (
            <button className="btn btn-buy" disabled={busy || !signal?.actionable || !plan?.ok}
              title={signal?.actionable ? "" : "flagged — not actionable"}
              onClick={() => act(() => openTrade(sym!))}>
              Paper buy
            </button>
          )}
        </div>
      </div>
      <div className="grow min-h-0 p-2">
        <CandleChart candles={candles} entry={entry} stop={stop} target={target} trailStop={trailStop} />
      </div>
    </div>
  );
}
