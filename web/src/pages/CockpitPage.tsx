import { useEffect, useMemo, useState } from "react";
import { getTrades } from "../api";
import DetailPanel from "../components/DetailPanel";
import Positions from "../components/Positions";
import ScannerTable from "../components/ScannerTable";
import Trades from "../components/Trades";
import type { Trade } from "../types";
import { useScanner, type ConnState } from "../useScanner";

function ConnBadge({ state, feed }: { state: ConnState; feed?: string }) {
  const map = {
    live: { c: "var(--green)", t: "LIVE" },
    connecting: { c: "var(--amber)", t: "CONNECTING" },
    down: { c: "var(--red)", t: "RECONNECTING" },
  }[state];
  return (
    <span className="badge inline-flex items-center gap-1.5" style={{ color: map.c, borderColor: map.c }}>
      <span className="live-dot" style={{ width: 7, height: 7, borderRadius: 99, background: map.c }} />
      {map.t}{feed ? ` · ${feed}` : ""}
    </span>
  );
}

const money = (v: number) => v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

function Kpi({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <span className="mono text-[12px]" style={{ color: "var(--muted)" }}>
      {label} <b style={{ color: color ?? "var(--text)" }}>{value}</b>
    </span>
  );
}

export default function CockpitPage() {
  const { data, state } = useScanner();
  const [selected, setSelected] = useState<string | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);

  const closedCount = data?.account.closed_trades ?? 0;
  useEffect(() => {
    getTrades().then(setTrades);
  }, [closedCount]);

  const acct = data?.account;
  const selSignal = useMemo(() => data?.signals.find((s) => s.symbol === selected) ?? null, [data, selected]);
  const selPos = useMemo(() => data?.positions.find((p) => p.symbol === selected) ?? null, [data, selected]);

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center gap-4 px-4 h-11 shrink-0" style={{ background: "var(--panel)", borderBottom: "1px solid var(--line)" }}>
        <ConnBadge state={state} feed={data?.feed} />
        {data && (
          <span className="badge" style={{ color: data.mode === "live" ? "var(--red)" : "var(--green)", borderColor: data.mode === "live" ? "var(--red)" : "var(--green)" }}>
            {data.mode}
          </span>
        )}
        {data && data.feed !== "mock" && (
          <span className="badge" style={{ color: "var(--amber)", borderColor: "var(--amber)" }}
                title="Massive plan is 15-min delayed — observation / paper only, NOT live-tradeable. Real-time needs the $189 plan.">
            ⏱ 15-min delayed · observe only
          </span>
        )}
        <div className="ml-auto flex items-center gap-5">
          {acct && (
            <>
              <Kpi label="equity" value={money(acct.equity)} />
              <Kpi label="day P&L" value={`${acct.day_pnl >= 0 ? "+" : ""}${money(acct.day_pnl)}`} color={acct.day_pnl < 0 ? "var(--red)" : "var(--green)"} />
              <Kpi label="unreal" value={`${acct.unrealized_pnl >= 0 ? "+" : ""}${money(acct.unrealized_pnl)}`} color={acct.unrealized_pnl < 0 ? "var(--red)" : "var(--green)"} />
              <Kpi label="open" value={String(acct.open_positions)} />
              {acct.daily_loss_limit_hit && <span className="badge" style={{ color: "var(--red)", borderColor: "var(--red)" }}>⛔ daily stop</span>}
            </>
          )}
        </div>
      </div>

      <main className="grow min-h-0 flex" style={{ background: "var(--bg)" }}>
        <section className="flex flex-col min-w-0" style={{ flex: "1 1 52%", borderRight: "1px solid var(--line)" }}>
          <div className="section-title px-3 py-1.5 shrink-0">Scanner{data ? ` · ${data.count} candidates` : ""}</div>
          <div className="grow min-h-0">
            <ScannerTable signals={data?.signals ?? []} selected={selected} onSelect={setSelected} />
          </div>
        </section>
        <section className="flex flex-col min-w-0" style={{ flex: "1 1 48%" }}>
          <div style={{ height: "44%", borderBottom: "1px solid var(--line)" }}>
            <DetailPanel signal={selSignal} position={selPos} />
          </div>
          <div style={{ height: "28%", borderBottom: "1px solid var(--line)" }}>
            <Positions positions={data?.positions ?? []} onSelect={setSelected} selected={selected} />
          </div>
          <div style={{ height: "28%" }}>
            <Trades trades={trades} />
          </div>
        </section>
      </main>

      <footer className="shrink-0 px-5 py-2 text-[11px] mono flex items-center gap-4" style={{ background: "var(--panel)", borderTop: "1px solid var(--line)", color: "var(--muted)" }}>
        <span>● actionable</span>
        <span style={{ opacity: 0.62 }}>○ flagged — don't chase</span>
        <span className="ml-auto">
          Paper-first · simulated broker · {data?.feed && data.feed !== "mock" ? `${data.feed} feed (15-min delayed)` : "mock data"}. Not advice.
        </span>
      </footer>
    </div>
  );
}
