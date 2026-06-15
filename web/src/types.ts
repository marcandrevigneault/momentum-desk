export interface Plan {
  ok: boolean;
  shares: number;
  entry: number;
  stop: number;
  target: number;
  trail_pct: number;
  risk_dollars: number;
  reasons: string[];
}

export interface Signal {
  symbol: string;
  score: number;
  last: number;
  gap_pct: number;
  relative_volume: number;
  extension_above_vwap_pct: number;
  float_millions: number | null;
  has_news: boolean;
  news_headline: string;
  actionable: boolean;
  flags: string[];
  held: boolean;
  plan?: Plan;
}

export interface Position {
  symbol: string;
  qty: number;
  entry: number;
  last: number;
  stop: number;
  target: number;
  high_water: number;
  unrealized_pnl: number;
}

export interface Account {
  equity: number;
  realized_pnl: number;
  unrealized_pnl: number;
  day_pnl: number;
  open_positions: number;
  closed_trades: number;
  daily_loss_limit_hit: boolean;
}

export interface ScanMessage {
  ts: number;
  feed: string;
  mode: string;
  count: number;
  signals: Signal[];
  account: Account;
  positions: Position[];
}

export interface Point {
  t: number;
  last: number;
  vwap: number;
}

export interface Candle {
  time: number;   // epoch seconds
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Trade {
  symbol: string;
  qty: number;
  entry: number;
  exit: number;
  exit_reason: string;
  gross_pnl: number;
  commission: number;
  pnl: number;
  opened_ts: number;
  closed_ts: number;
}

export interface BacktestMetrics {
  trades: number;
  win_rate: number;
  profit_factor: number;
  avg_win: number;
  avg_loss: number;
  expectancy: number;
  expectancy_r: number;
  total_pnl: number;
  return_pct: number;
  max_drawdown: number;
  max_drawdown_pct: number;
}

export interface BacktestTrade {
  symbol: string;
  day: string;
  entry: number;
  exit: number;
  shares: number;
  exit_reason: string;
  r_multiple: number;
  pnl: number;
}

export interface PeriodRow {
  period: string;
  trades: number;
  wins: number;
  win_rate: number;
  pnl: number;
  cum_pnl: number;
}

export interface Job {
  id: string;
  status: string;          // running | done | error
  elapsed: number;
  progress: number;        // 0..1
  params: Record<string, number | string>;
  error?: string;
}

export interface RunSummary {
  id: string;
  ts: number;
  kind: string;
  synthetic?: boolean;
  session?: string;
  days?: number;
  trades?: number;
  expectancy_r?: number;
  total_pnl?: number;
  max_drawdown_pct?: number;
  target_r?: number;
  time_exit_tod?: number;
}

export interface EdgeDecile {
  lo: number;
  hi: number;
  n: number;
  mean_fwd_r: number;
}

export interface EdgeFeature {
  name: string;
  kind: string;        // "static" | "dynamic"
  desc: string;
  n: number;
  ic: number;          // Spearman rank correlation with forward R
  lift_spread: number; // top-decile mean R minus bottom-decile mean R
  deciles: EdgeDecile[];
}

export interface EdgeSessionScreen {
  session: string;
  n_events: number;
  baseline_fwd_r: number;
  win_rate: number;
  features: EdgeFeature[];
}

export interface EdgeScreen {
  generated: string | null;
  days: number | null;
  data: string | null;
  source: string;     // "live" | "snapshot"
  sessions: Record<string, EdgeSessionScreen>;
}

export interface ExitMetrics {
  policy: string;
  desc: string;
  n: number;
  expectancy_r: number;
  win_rate: number;
  profit_factor: number;
  avg_win_r: number;
  avg_loss_r: number;
  median_r: number;
  best_r: number;
  worst_r: number;
  max_dd_r: number;
  avg_hold_bars: number;
  exit_reasons: Record<string, number>;
}

export interface ExitLabSession {
  session: string;
  n_events: number;
  policies: ExitMetrics[];
}

export interface ExitLab {
  generated: string | null;
  days: number | null;
  data: string | null;
  slippage: number | null;
  source: string;
  sessions: Record<string, ExitLabSession>;
}

export interface BacktestRun {
  synthetic: boolean;
  session: string;
  days: number;
  metrics: BacktestMetrics;
  equity_curve: number[];
  trades: BacktestTrade[];
  monthly?: PeriodRow[];
  yearly?: PeriodRow[];
  available?: boolean;        // for the real-run loader
}
