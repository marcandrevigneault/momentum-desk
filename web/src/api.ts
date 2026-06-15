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

export async function getSimRun(): Promise<import("./types").SimRun> {
  const r = await fetch("/api/simrun");
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
