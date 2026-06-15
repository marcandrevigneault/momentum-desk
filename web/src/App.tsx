import { useScanner, type ConnState } from "./useScanner";
import ScannerTable from "./components/ScannerTable";

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

function money(v: number) {
  const s = v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
  return s;
}

export default function App() {
  const { data, state } = useScanner();
  const acct = data?.account;
  const pnl = acct?.realized_pnl_today ?? 0;

  return (
    <div className="h-full flex flex-col">
      <header
        className="flex items-center gap-4 px-5 h-14 shrink-0"
        style={{ background: "var(--panel)", borderBottom: "1px solid var(--line)" }}
      >
        <h1 className="m-0 text-[17px] font-bold tracking-tight">
          Momentum&nbsp;Desk
          <span className="mono text-[11px] font-medium ml-2" style={{ color: "var(--muted)" }}>
            low-float scanner
          </span>
        </h1>

        <ConnBadge state={state} feed={data?.feed} />
        {data && (
          <span
            className="badge"
            style={{
              color: data.mode === "live" ? "var(--red)" : "var(--green)",
              borderColor: data.mode === "live" ? "var(--red)" : "var(--green)",
            }}
          >
            {data.mode}
          </span>
        )}

        <div className="ml-auto flex items-center gap-5 mono text-[12px]">
          {acct && (
            <>
              <span style={{ color: "var(--muted)" }}>
                equity <b style={{ color: "var(--text)" }}>{money(acct.equity)}</b>
              </span>
              <span style={{ color: "var(--muted)" }}>
                day P&L{" "}
                <b style={{ color: pnl < 0 ? "var(--red)" : "var(--green)" }}>
                  {pnl >= 0 ? "+" : ""}{money(pnl)}
                </b>
              </span>
              {acct.daily_loss_limit_hit && (
                <span className="badge" style={{ color: "var(--red)", borderColor: "var(--red)" }}>
                  ⛔ daily stop hit
                </span>
              )}
            </>
          )}
          <span style={{ color: "var(--muted)" }}>
            {data ? `${data.count} candidates` : "—"}
          </span>
        </div>
      </header>

      <main className="grow min-h-0" style={{ background: "var(--bg)" }}>
        <ScannerTable signals={data?.signals ?? []} />
      </main>

      <footer
        className="shrink-0 px-5 py-2 text-[11px] mono flex items-center gap-4"
        style={{ background: "var(--panel)", borderTop: "1px solid var(--line)", color: "var(--muted)" }}
      >
        <span>● actionable</span>
        <span style={{ opacity: 0.62 }}>○ found but flagged — don't chase</span>
        <span className="ml-auto">
          Paper-first. Not advice. Mock data unless a real feed is configured.
        </span>
      </footer>
    </div>
  );
}
