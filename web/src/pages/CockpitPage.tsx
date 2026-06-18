import { useEffect, useMemo, useState } from "react";
import { getIbkrPortfolio, getLiveIntent, getTrades, type IbkrPortfolio, type LiveIntent } from "../api";
import DetailPanel from "../components/DetailPanel";
import Positions from "../components/Positions";
import ScannerTable from "../components/ScannerTable";
import Trades from "../components/Trades";
import type { ScanMessage, Trade } from "../types";
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

const fmtAge = (s: number) => (s < 90 ? `${Math.round(s)}s` : `${Math.round(s / 60)}m`);

/** Measured feed freshness — the real data age, not a hardcoded assumption. Only
 *  flags "delayed" during the regular session, when a live feed should be fresh. */
function FeedFreshness({ data }: { data: ScanMessage }) {
  const phase = data.market_phase;
  const age = data.feed_age_s;
  if (phase && phase !== "regular") {
    const label = phase === "extended" ? "extended hours" : "market closed";
    return (
      <span className="badge" style={{ color: "var(--muted)", borderColor: "var(--line)" }}
            title={`US market ${label}. ${age != null ? `Last print ${fmtAge(age)} ago.` : ""} Feed delay is only measurable during 09:30–16:00 ET.`}>
        🌙 {label}
      </span>
    );
  }
  if (age == null) return null;                        // regular hours but no timestamp yet
  const fresh = age <= 120;                            // a real-time feed prints sub-minute
  const c = fresh ? "var(--green)" : "var(--amber)";
  return (
    <span className="badge" style={{ color: c, borderColor: c }}
          title={fresh
            ? `Real-time: last market print ${fmtAge(age)} ago.`
            : `Feed is ~${fmtAge(age)} behind the market — delayed tier. Paper/observe only at this latency.`}>
      {fresh ? "⚡ real-time" : "⏱ delayed"} · {fmtAge(age)}
    </span>
  );
}

function LiveEngineBadge() {
  const [li, setLi] = useState<LiveIntent | null>(null);
  useEffect(() => {
    let on = true;
    const tick = () => getLiveIntent().then((r) => on && setLi(r)).catch(() => {});
    tick();
    const id = setInterval(tick, 10000);
    return () => { on = false; clearInterval(id); };
  }, []);
  if (!li?.available) return null;   // engine off / multi-leg active → nothing to show
  const pnl = li.day_pnl ?? 0;
  const c = li.armed ? "var(--red)" : "var(--blue, #5b8def)";
  return (
    <span className="badge inline-flex items-center gap-1.5" style={{ color: c, borderColor: c }}
          title={`Reconciled engine on the live tape for "${li.strategy}" — ${li.armed ? "ARMED" : "dry-run, nothing transmitted"}. Watching ${li.watching?.length ?? 0}, holding ${li.holding?.length ?? 0}, ${li.closed?.length ?? 0} closed today.`}>
      🤖 engine {li.armed ? "ARMED" : "dry-run"} · watch {li.watching?.length ?? 0} · hold {li.holding?.length ?? 0} · {pnl >= 0 ? "+" : ""}{money(pnl)}
    </span>
  );
}

function IbkrBadge() {
  const [pf, setPf] = useState<IbkrPortfolio | null>(null);
  useEffect(() => {
    let on = true;
    const tick = () => getIbkrPortfolio().then((r) => on && setPf(r)).catch(() => {});
    tick();
    const id = setInterval(tick, 15000);
    return () => { on = false; clearInterval(id); };
  }, []);
  if (!pf?.enabled) return null;             // IBKR off → nothing to show
  if (!pf.ok) {
    return (
      <span className="badge" style={{ color: "var(--amber)", borderColor: "var(--amber)" }}
            title={pf.reason}>🏦 IBKR gateway not ready</span>
    );
  }
  const c = pf.paper ? "var(--green)" : "var(--red)";
  const npos = pf.positions?.length ?? 0;
  return (
    <span className="badge inline-flex items-center gap-1.5" style={{ color: c, borderColor: c }}
          title={`Real IBKR ${pf.paper ? "paper" : "LIVE"} account ${pf.account_id} — read-only. ${npos} open position(s). Cash ${money(pf.cash ?? 0)}.`}>
      🏦 IBKR {pf.paper ? "paper" : "LIVE"} {pf.account_id} · NAV {money(pf.nav ?? 0)} · {npos} pos
    </span>
  );
}

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
        {data && data.feed !== "mock" && <FeedFreshness data={data} />}
        <LiveEngineBadge />
        <IbkrBadge />
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
