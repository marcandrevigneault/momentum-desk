# Edge-Detection Platform — design & roadmap

> **Objective.** Momentum Desk is **not** a tool you trade with — it is a platform
> that *discovers and explains where the edge is*. Interpretability is the
> deliverable: you must be able to **see** the variables a model uses, whether a
> parameter is static or moves with price/volume, what the exit mechanics are, and
> whether an ML model beats the rules **and why**.

This doc is the living spec. Code lives under `momentum_desk/edge/`.

## Organizing principle

Every strategy is a transparent pipeline:

```
features  →  entry rule  →  exit policy  →  sizing
```

…where **every parameter is inspectable and may be static *or* a function of
state**, and **every claimed edge must survive an anti-self-deception gauntlet**
before it is believed.

## The six layers

1. **Feature library** — every variable is a named, documented function
   `bar-window → value`, tagged `static` (known at the open) or `dynamic`
   (recomputed each bar). This is the vocabulary everything else is written in.
2. **Composable rule trees** — strategies expressed declaratively and rendered
   back in plain English. Any threshold can be a constant *or* a function of
   state (`stop = k·ATR`, `size ∝ 1/volatility`). Static and dynamic variants
   compete in the same harness.
3. **Exit-policy lab** — fixed-R TP, hard stop, %-trail, ATR-trail,
   structure-trail (swing lows), VWAP-loss, time stop — run the *same entries*
   through *different exits* to find which converts the signal best. Exits are
   where most of the edge and variance live (optimal stopping).
4. **Interpretable ML contender** — LightGBM on the feature library with **SHAP**
   for global importance *and* per-trade attributions. No opaque nets (explicit
   product decision): every decision must be auditable. Held to the same gauntlet
   as the rules.
5. **Anti-self-deception evaluation gauntlet** — purged + embargoed walk-forward
   CV, **Deflated/Probabilistic Sharpe** (multiple-testing correction — sweeping
   configs inflates the winner), an untouched OOS holdout, honest fills + slippage
   stress, regime breakdowns, bootstrap CIs, and univariate decile-lift screening.
6. **Human-readable UI** — Feature Catalog (with live distributions), Strategy
   Inspector (rule tree in English, static/dynamic badges), **Edge Report** card
   (expectancy ± CI, decile-lift per feature, SHAP, sensitivity heatmaps, explicit
   failure modes), deflated Leaderboard, and "would-have-traded" examples drawn on
   the candle viewer.

## Current model — honest inventory (2026-06-16)

- **Features:** partly dynamic already — RVOL, VWAP-extension and HOD are
  recomputed each minute. But the **parameters are static**: `target_r`,
  `stop_buffer_pct`, the 8% anti-chase, the RVOL minimum are fixed constants.
  Making these inspectable and optionally dynamic is a core goal.
- **Exits:** take-profit (R-multiple), hard stop, a ratcheting **%** trailing stop
  (in `PaperDesk`), and a time stop all exist — but they are **not compared
  head-to-head**, and the trail is a simple % ratchet, not ATR/structure-based.
- **ML/NN:** none trades today; the "ML strategy" was only a selector placeholder.

## Phasing

| Phase | Deliverable | Status |
|---|---|---|
| **1** | Feature library + univariate **decile-lift screener** + Feature Catalog | done (PR #20) |
| **2** | Exit-policy lab (same entries, swappable exits, compared) | done |
| **3** | Evaluation gauntlet (purged CV, deflated Sharpe, regime, bootstrap CIs) | done |
| **3.5** | End-to-end account simulation (`portfolio.py`) — detect → size → trail → exit, with concurrency/capital caps + slippage stress | done |
| 4 | Strategy Inspector + Edge Report UI | planned |
| 5 | LightGBM + SHAP contender | planned |

## Phase 1 detail

`momentum_desk/edge/features.py` — a `FeatureContext` (everything knowable at the
entry decision point, point-in-time) and a `FEATURES` registry of named, tagged,
documented feature functions.

`momentum_desk/edge/screen.py` — builds a dataset of *(features, forward outcome)*
events for a session, where the **entry trigger is fixed** (session breakout) and
the discretionary RVOL/anti-chase filters are **recorded as features rather than
applied** — so we can measure whether they actually help. Forward outcome is a
**standardized** R-multiple (fixed stop = recent low, fixed 2R target, time cap)
so the screen measures *entry quality* with exit policy held constant; MFE/MAE in
R are reported too. Per feature it reports a Spearman information coefficient and a
decile-lift table (mean forward R per decile) — the first readable answer to
"which variables carry edge."

Run side by side on **premarket-gapper** and **intraday-active** universes
(`scripts/screen_edge.py`). Synthetic data validates the machinery; real
Massive/Polygon data (cached) produces the actual screen.
