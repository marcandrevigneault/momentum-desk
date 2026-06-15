# Momentum Desk

A fast low-float momentum **scanner** with a **mechanical risk engine**, built
**paper-first**, designed to connect to Interactive Brokers. It scans for the
documented Warrior-style setup — low float + high relative volume + a news
catalyst in a tradable price band — and, just as importantly, refuses to let
you chase the part of a move where you become *exit liquidity*.

```bash
python -m momentum_desk.cli        # runs now, no install, no credentials (mock data)
```

---

## Read this before you trust it with a dollar

This project started from the idea of "copy-trading Ross Cameron." Three facts
shaped what it actually is:

1. **You cannot reverse-engineer his trades from earnings reports.** Verified
   earnings pages are aggregate P&L and statement screenshots — not a
   timestamped, ticker-level trade log — and they're after the fact. There is
   no live feed of anyone's trades; by the time a trade is public you're late.
   So this tool encodes the *setup criteria*, not "his trades."

2. **The FTC measured the outcome of following this strategy.** In 2022 the FTC
   charged Warrior Trading / Ross Cameron with deceptive earnings claims; they
   settled for **$3M** and the FTC found *"the vast majority of customers
   actually lost money trading."* ~$2.9M was returned to harmed customers.
   ([press release](https://www.ftc.gov/news-events/news/press-releases/2022/04/federal-trade-commission-cracks-down-warrior-trading-misleading-consumers-false-investment-promises) ·
   [refunds](https://www.ftc.gov/news-events/news/press-releases/2023/01/ftc-returns-more-29-million-consumers-harmed-warrior-trading))

3. **A faster way to take losing trades loses money faster.** If you "usually
   lose and become exit liquidity," the fix isn't a better entry alert — it's
   mechanical risk control, validation on historical data, and a guard that
   tells you when your own order is too big for the tape. Those are the parts
   of this repo that matter most.

**This is not financial advice and carries no promise of profit. Trade on paper
until a rule set is proven, and never risk money you can't lose.**

---

## How it works

```
data feed → scanner (filters + flags + score) → risk engine (sizing + guards) → you → IBKR (paper)
   ▲                                                                                      
   └── pluggable: mock today; polygon / finnhub / ibkr next
```

- **`models.py`** — `Snapshot` in, `Signal` out; derived metrics (gap %, RVOL,
  extension above VWAP, float).
- **`adapters/`** — `MarketDataAdapter` protocol + a `MockReplayAdapter` that
  simulates a morning of low-float gappers so everything runs with no feed.
- **`scanner.py`** — candidate filters (price band, low float, RVOL, gap, news)
  plus **anti-chase flags**: `EXTENDED` (too far above VWAP), `HALTED`,
  `UNKNOWN_FLOAT`. Ranks fresh setups above chased ones.
- **`risk.py`** — sizes every trade from its **stop** (never guessed), enforces
  a **daily-loss circuit breaker**, a per-trade risk cap, a position cap, and
  the **liquidity guard** ("you would be the liquidity: your size > 1% of
  today's volume").
- **`cli.py`** — console demo of the full pipeline.

In the demo you can watch a runner flip from actionable (`✓`) to
`EXTENDED — don't chase` (`·`) as it stretches above VWAP. That flip is the
whole point.

## Where a real edge might live (and where it doesn't)

- **Anti-chase first.** Most blow-ups are buying a stock already up 150%. The
  extension-above-VWAP guard is the highest-value rule here.
- **Exits and sizing, not entries.** The "algorithm in his head" is mostly cut
  losers fast, size by stop distance, let a few winners pay for many losers.
- **First hour + catalyst.** Most of these moves are 9:30–10:30 ET on news.
- **Liquidity reality.** On a thin $2 name your market order moves the print.
- **Honesty about expectancy.** Most "obvious" momentum rules are negative after
  slippage. The point of the (planned) backtester is to *find out* on history,
  not with your account.

## Roadmap

- [x] Core models, pluggable data adapter, mock feed
- [x] Scanner with anti-chase flags + scoring
- [x] Mechanical risk engine (sizing, daily stop, liquidity guard)
- [ ] Real-time web dashboard (streaming table, sub-second)
- [ ] Real market data adapter (polygon / finnhub / IBKR scanner)
- [ ] IBKR connection — **paper account first** (quotes, then order routing)
- [ ] Backtester on historical low-float gappers + expectancy report
- [ ] Trade journal (every signal, decision, and fill logged for review)

## Safety defaults

`mode: paper` and `data_feed: mock` ship as defaults. Credentials live only in
`config.yaml`, which is gitignored. Going live is a deliberate, explicit switch.
