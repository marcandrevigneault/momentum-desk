import { useEffect, useState } from "react";
import { getBars } from "../api";
import type { Candle } from "../types";
import CandleChart, { type ChartMarker } from "./CandleChart";

/** Focused view of ONE historical trade: that session's 1-minute candles with
 *  open/close position arrows and entry/exit price lines. Opened by clicking a
 *  row in the Lab trade log. Pure view — fetches one day of bars, changes nothing. */

export interface FocusTrade {
  day: string;
  symbol: string;
  entry_tod: number;
  exit_tod: number;
  entry: number;
  exit: number;
  shares: number;
  pnl: number;
  r_multiple: number;
  exit_reason: string;
}

const money2 = (v: number) =>
  (v ?? 0).toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2, signDisplay: "always" });
const tod = (mins: number) =>
  `${String(Math.floor((mins ?? 0) / 60)).padStart(2, "0")}:${String((mins ?? 0) % 60).padStart(2, "0")}`;

/** ET minute-of-day for an epoch-seconds bar time (DST-correct via Intl). */
function etMinutes(epochSec: number): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", hour12: false, hour: "2-digit", minute: "2-digit",
  }).formatToParts(new Date(epochSec * 1000));
  const h = +(parts.find((p) => p.type === "hour")?.value ?? 0) % 24;
  const m = +(parts.find((p) => p.type === "minute")?.value ?? 0);
  return h * 60 + m;
}

/** The bar matching an ET time-of-day (exact, else nearest). */
function barAt(candles: Candle[], targetTod: number): Candle | null {
  if (!candles.length) return null;
  let best: Candle = candles[0];
  let bestDist = Infinity;
  for (const c of candles) {
    const d = Math.abs(etMinutes(c.time) - targetTod);
    if (d < bestDist) { best = c; bestDist = d; }
    if (d === 0) break;
  }
  return bestDist <= 5 ? best : null;   // >5 min off = don't pretend we found it
}

export default function TradeFocus({ trade, onClose }: { trade: FocusTrade; onClose: () => void }) {
  const [candles, setCandles] = useState<Candle[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let on = true;
    setCandles([]); setErr(null);
    getBars(trade.symbol, "1m", trade.day)
      .then((c) => { if (on) { setCandles(c); if (!c.length) setErr("no bars for this day (synthetic run, or data gap)"); } })
      .catch((e) => on && setErr(String(e)));
    return () => { on = false; };
  }, [trade.symbol, trade.day]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const entryBar = barAt(candles, trade.entry_tod);
  const exitBar = barAt(candles, trade.exit_tod);
  const win = (trade.pnl ?? 0) >= 0;
  const markers: ChartMarker[] = [];
  if (entryBar) markers.push({
    time: entryBar.time, position: "belowBar", color: "#60a5fa", shape: "arrowUp",
    text: `BUY ${trade.shares} @ ${trade.entry}`,
  });
  if (exitBar) markers.push({
    time: exitBar.time, position: "aboveBar", color: win ? "#34d399" : "#f87171", shape: "arrowDown",
    text: `SELL @ ${trade.exit} (${trade.exit_reason})`,
  });

  return (
    <div className="fixed inset-0 z-40 grid place-items-center p-4" style={{ background: "rgba(0,0,0,.65)" }}
      onClick={onClose}>
      <div className="rounded-lg flex flex-col w-full" onClick={(e) => e.stopPropagation()}
        style={{ background: "var(--panel)", border: "1px solid var(--line)", maxWidth: 900, height: "min(75vh, 560px)" }}>
        <div className="flex items-center gap-3 px-4 py-2.5 shrink-0 flex-wrap" style={{ borderBottom: "1px solid var(--line)" }}>
          <span className="font-bold text-[14px]">{trade.symbol}</span>
          <span className="mono text-[11px]" style={{ color: "var(--muted)" }}>{trade.day}</span>
          <span className="mono text-[11px]">{tod(trade.entry_tod)} → {tod(trade.exit_tod)}</span>
          <span className="mono text-[11px]">{trade.shares} sh @ {trade.entry} → {trade.exit}</span>
          <span className="mono text-[11px] px-2 py-0.5 rounded" style={{ color: "var(--muted)", border: "1px solid var(--line)" }}>
            {trade.exit_reason}
          </span>
          <span className="mono text-[12px] font-semibold" style={{ color: win ? "var(--green)" : "var(--red)" }}>
            {money2(trade.pnl)} · {(trade.r_multiple ?? 0).toFixed(2)}R
          </span>
          <button onClick={onClose} className="ml-auto mono text-[11px] px-2 py-1 rounded"
            style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--muted)" }}
            title="back to strategy (Esc, browser Back, or click outside)">← back to strategy</button>
        </div>
        <div className="grow min-h-0 relative">
          <CandleChart candles={candles} entry={trade.entry} exitPrice={trade.exit} markers={markers} />
          {err && (
            <div className="absolute inset-0 grid place-items-center text-[12px]" style={{ color: "var(--muted)" }}>{err}</div>
          )}
        </div>
        <div className="px-4 py-1.5 text-[10px] shrink-0" style={{ color: "var(--muted)", borderTop: "1px solid var(--line)" }}>
          ▲ open · ▼ close — 1-minute session candles. Simulated fills carry modeled slippage, so marker
          prices can sit slightly off the tape.
        </div>
      </div>
    </div>
  );
}
