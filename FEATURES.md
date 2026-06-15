# Feature roadmap — ranked

Ordered by what most protects and improves a trader who keeps becoming exit
liquidity. The theme: **validate and review before you risk; act mechanically,
not emotionally.** Each item becomes a branch + PR; PRs must pass CI.

| # | Feature | Why this rank | Risk | Branch |
|---|---------|---------------|------|--------|
| 1 | **Trade journal & session logging** | You only improve by reviewing decisions. Persist every signal, decision, risk-plan reason, and fill (JSONL) + a review CLI. Nothing else compounds without this feedback loop. | none (local) | `feat/trade-journal` |
| 2 | **IBKR paper connection** | The original objective. Turns the read-only desk into one that can place orders — **paper-only**, behind a hard live-guard, sized by the existing risk engine. | high → paper-first, dry-run default | `feat/ibkr-paper` |
| 3 | **Backtest robustness (sweep + walk-forward)** | One equity curve proves nothing. Grid over target/stop/hold, split in/out-of-sample, report stability so you don't overfit a fluke. | none (local) | `feat/backtest-robustness` |
| 4 | **Halt/LULD awareness + more setups** | LULD halts define low-float trading; ignoring them is dangerous. Add halt modeling + VWAP-reclaim / first-pullback setups as pluggable strategies. | medium | `feat/halts-and-setups` |
| 5 | **Live position & P&L tracking** | Close the risk loop: real open positions, unrealized P&L, and feed realized fills into the daily-loss circuit breaker. | high (needs broker) | `feat/positions-pnl` |
| 6 | **Dashboard UX: detail panel, sparkline, alerts** | Click a row → intraday chart + plan reasons; alert on a fresh actionable signal so you act early instead of chasing. | low | `feat/dashboard-detail` |
| 7 | **Config & secrets hardening + deploy docs** | Validate config, document the GHCR image deploy, env-var secrets. Operational polish. | low | `feat/ops-hardening` |

## Principles every feature inherits
- **Paper/mock defaults; live is an explicit, deliberate switch.**
- **The risk engine is the boss** — sizing, daily-loss breaker, and liquidity
  guard apply everywhere, including backtests and live.
- **No silent failure** in anything touching money or orders.
- **Tests on the money-critical paths.** Green CI before merge.
