import type { Point, Trade } from "./types";

/** Thin REST helpers for the cockpit's actions and backfills. Same-origin, so
 *  Basic-Auth creds (when deployed) ride along automatically. */

export async function openTrade(symbol: string): Promise<{ ok: boolean; reasons?: string[] }> {
  const r = await fetch(`/api/trade/open/${symbol}`, { method: "POST" });
  return r.json();
}

export async function closeTrade(symbol: string): Promise<{ ok: boolean; pnl?: number }> {
  const r = await fetch(`/api/trade/close/${symbol}`, { method: "POST" });
  return r.json();
}

export async function getHistory(symbol: string): Promise<Point[]> {
  const r = await fetch(`/api/history/${symbol}`);
  return (await r.json()).points ?? [];
}

export async function getBars(symbol: string, tf: string): Promise<import("./types").Candle[]> {
  const r = await fetch(`/api/bars/${symbol}?tf=${tf}`);
  return (await r.json()).candles ?? [];
}

export async function getEdge(): Promise<import("./types").EdgeScreen> {
  const r = await fetch("/api/edge");
  return r.json();
}

export async function getExitLab(): Promise<import("./types").ExitLab> {
  const r = await fetch("/api/exitlab");
  return r.json();
}

export async function getGauntlet(): Promise<import("./types").Gauntlet> {
  const r = await fetch("/api/gauntlet");
  return r.json();
}

export async function getSimRun(window: string = "1y", compound = false): Promise<import("./types").SimRun> {
  const r = await fetch(`/api/simrun?window=${window}&compound=${compound}`);
  return r.json();
}

export async function getCombo(window: string = "1y"): Promise<import("./types").ComboRun> {
  const r = await fetch(`/api/combo?window=${window}`);
  return r.json();
}

export async function getCombos(window: string = "1y"): Promise<import("./types").CombosSnapshot> {
  const r = await fetch(`/api/combos?window=${window}`);
  return r.json();
}

export async function getOptimize(): Promise<import("./types").OptimizeSnapshot> {
  const r = await fetch("/api/optimize");
  return r.json();
}

export async function getCombosOptimize(): Promise<{ source: string; best_by_combo?: Record<string, { intraday_exit: string; max_concurrent: number; daily_sharpe: number }>; best?: { combo: string }; best_beats_intraday_only?: boolean }> {
  const r = await fetch("/api/combos-optimize");
  return r.json();
}

export async function getRules(): Promise<import("./types").RulesSnapshot> {
  const r = await fetch("/api/rules");
  return r.json();
}

export async function getTunerMeta(): Promise<{ sessions: string[]; policies: string[]; days: number | null; available: boolean }> {
  const r = await fetch("/api/tuner");
  return r.json();
}

export async function evaluateConfig(p: {
  session: string; max_ext: number | null; rvol_min: number; rvol_max: number | null; min_move: number; exit: string;
}): Promise<{ n: number; expectancy_r: number; win_rate: number; profit_factor: number; daily_sharpe: number }> {
  const q = new URLSearchParams({ session: p.session, rvol_min: String(p.rvol_min), min_move: String(p.min_move), exit: p.exit });
  if (p.max_ext != null) q.set("max_ext", String(p.max_ext));
  if (p.rvol_max != null) q.set("rvol_max", String(p.rvol_max));
  const r = await fetch(`/api/evaluate?${q}`);
  return r.json();
}

export async function getActiveStrategy(): Promise<import("./types").ActiveStrategy> {
  const r = await fetch("/api/active-strategy");
  return r.json();
}

export async function setActiveStrategy(active: string, label: string): Promise<import("./types").ActiveStrategy> {
  const r = await fetch("/api/active-strategy", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ active, label }),
  });
  return r.json();
}

export async function getTrades(): Promise<Trade[]> {
  const r = await fetch("/api/trades");
  return (await r.json()).trades ?? [];
}

export interface BacktestParams {
  session: string;
  days: number;
  target_r: number;
  slippage_pct: number;
  max_hold: number;
  time_exit_tod: number;
}

export async function runBacktest(p: BacktestParams) {
  const q = new URLSearchParams({
    session: p.session, days: String(p.days), target_r: String(p.target_r),
    slippage_pct: String(p.slippage_pct), max_hold: String(p.max_hold),
    time_exit_tod: String(p.time_exit_tod),
  });
  const r = await fetch(`/api/backtest?${q}`, { method: "POST" });
  return r.json();
}

export async function listRuns(): Promise<import("./types").RunSummary[]> {
  const r = await fetch("/api/backtest/runs");
  return (await r.json()).runs ?? [];
}

export async function getRun(id: string) {
  const r = await fetch(`/api/backtest/runs/${id}`);
  return (await r.json()).result;
}

export async function deleteRun(id: string) {
  await fetch(`/api/backtest/runs/${id}`, { method: "DELETE" });
}

export async function listJobs(): Promise<import("./types").Job[]> {
  const r = await fetch("/api/backtest/jobs");
  return (await r.json()).jobs ?? [];
}

export async function launchRealBacktest(p: BacktestParams) {
  const q = new URLSearchParams({
    session: p.session, days: String(p.days), target_r: String(p.target_r),
    slippage_pct: String(p.slippage_pct), max_hold: String(p.max_hold),
    time_exit_tod: String(p.time_exit_tod),
  });
  const r = await fetch(`/api/backtest/launch?${q}`, { method: "POST" });
  return r.json();
}

export async function pollJob(id: string) {
  const r = await fetch(`/api/backtest/job/${id}`);
  return r.json();
}
