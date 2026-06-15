export interface Plan {
  ok: boolean;
  shares: number;
  entry: number;
  stop: number;
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
  plan?: Plan;
}

export interface Account {
  equity: number;
  realized_pnl_today: number;
  daily_loss_limit_hit: boolean;
}

export interface ScanMessage {
  ts: number;
  feed: string;
  mode: string;
  count: number;
  signals: Signal[];
  account: Account;
}
