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
