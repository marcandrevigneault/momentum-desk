import { useState } from "react";
import BacktesterPage from "./pages/BacktesterPage";
import CockpitPage from "./pages/CockpitPage";

type Page = "cockpit" | "backtester";

const NAV: { id: Page; label: string; icon: string; hint: string }[] = [
  { id: "cockpit", label: "Cockpit", icon: "▦", hint: "live scanner, charts, paper trading" },
  { id: "backtester", label: "Backtester", icon: "📈", hint: "run + visualize a strategy backtest" },
];

export default function App() {
  const [page, setPage] = useState<Page>("cockpit");
  const [menu, setMenu] = useState(false);
  const active = NAV.find((n) => n.id === page)!;

  return (
    <div className="h-full flex flex-col">
      {/* shell top bar */}
      <header className="flex items-center gap-3 px-4 h-12 shrink-0 relative z-30" style={{ background: "var(--panel)", borderBottom: "1px solid var(--line)" }}>
        <button
          className="hamburger"
          aria-label="menu"
          onClick={() => setMenu((v) => !v)}
        >
          <span /><span /><span />
        </button>
        <h1 className="m-0 text-[16px] font-bold tracking-tight">
          Momentum&nbsp;Desk
          <span className="mono text-[11px] font-medium ml-2" style={{ color: "var(--muted)" }}>{active.label.toLowerCase()}</span>
        </h1>
      </header>

      {/* slide-in nav drawer + backdrop */}
      {menu && <div className="fixed inset-0 z-20" style={{ background: "rgba(0,0,0,.5)" }} onClick={() => setMenu(false)} />}
      <nav
        className="fixed top-0 left-0 h-full z-30 flex flex-col transition-transform duration-200"
        style={{
          width: 248, background: "var(--panel)", borderRight: "1px solid var(--line)",
          transform: menu ? "translateX(0)" : "translateX(-100%)",
        }}
      >
        <div className="h-12 flex items-center px-4 font-bold text-[15px] shrink-0" style={{ borderBottom: "1px solid var(--line)" }}>
          Momentum Desk
        </div>
        <div className="p-2 flex flex-col gap-1">
          {NAV.map((n) => (
            <button
              key={n.id}
              onClick={() => { setPage(n.id); setMenu(false); }}
              className="text-left rounded-lg px-3 py-2.5 flex items-start gap-3"
              style={{
                background: page === n.id ? "var(--panel-2)" : "transparent",
                boxShadow: page === n.id ? "inset 2px 0 0 var(--green)" : undefined,
              }}
            >
              <span className="text-[15px]">{n.icon}</span>
              <span className="flex flex-col">
                <span className="font-semibold text-[13px]">{n.label}</span>
                <span className="text-[11px]" style={{ color: "var(--muted)" }}>{n.hint}</span>
              </span>
            </button>
          ))}
        </div>
        <div className="mt-auto p-3 text-[10px] mono" style={{ color: "var(--muted)", borderTop: "1px solid var(--line)" }}>
          paper-first · not advice
        </div>
      </nav>

      {/* active page */}
      <div className="grow min-h-0">
        {page === "cockpit" ? <CockpitPage /> : <BacktesterPage />}
      </div>
    </div>
  );
}
