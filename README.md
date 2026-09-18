# PARALLAX — Autonomous Human-Intelligence Trading System

A disciplined, deterministic decision-making system for **Indian index markets
(NIFTY / BANKNIFTY / FINNIFTY) and crypto perpetuals**, built to the
[PARALLAX Master System Design](C:\Users\tgowd\Downloads\PARALLAX_Master_System_Design.docx).

PARALLAX is **not a signal bot**. It perceives the market, reconstructs
context, forms and challenges competing hypotheses, quantifies uncertainty,
acts only when a validated edge exists, vets every trade through an
independent risk gate, and reflects on outcomes — remaining disciplined when
the correct action is to do nothing.

> A language model may participate in reasoning and communication, but it is
> never the sole source of truth for prices, positions, risk limits, or
> executable order parameters.

---

## Architecture (design doc → code)

    contracts/     §6–§23  canonical typed data (dataclasses + enums), boundary validators
    core/
      perception/  §6     indicators + SMC structure + liquidity → MarketState
      context/     §6     multi-timeframe bias + regime classification
      knowledge/   §3     trading concepts corpus (semantic memory seed)
      hypotheses/  §9     LONG/SHORT/RANGE/VOL + always a NO-TRADE hypothesis
      reasoning/   §7     evidence, counter-evidence, statistical edge (EV)
      metacognition §8    self-audit, confidence calibration, bias firewall
      decision/    §10    WAIT / TRADE / HOLD / EXIT / ABORT
      risk/        §11    independent hard gate (veto, sizing, kill switch)
      execution/   §12    typed order-intent boundary, reconciliation
      memory/      §13    episodic / semantic / error stores with weighting
      reflection/  §14    post-trade decision-quality audit (no auto self-modify)
      brain/       §18    DeepSeek reasoning brain (advisory/veto-only) + multi-validator
      observability §23   append-only audit trail
    executive/     §20    state machine, modes, orchestration, kill switch
    adapters/      §12,§16 §19  market-data gateway, paper broker, Telegram, storage
    apps/
      worker/      §25    replay / live loop
      research/    §15    event-driven backtest + walk-forward (real costs)

The pipeline per evaluation: **perception → context → hypotheses →
metacognition → decision → independent risk gate → execution → reflection**.

---

## Quick start

```bash
cd C:\Parallax

# describe what the market is doing (structured state only)
python -m parallax describe --csv "C:\PrOxyTradingTerminal\data\NIFTY_5m.csv" --instrument NIFTY

# backtest on real data (realistic fees + slippage, stop-first exits)
python -m parallax backtest --csv "C:\PrOxyTradingTerminal\data\NIFTY_5m.csv" --instrument NIFTY --max-bars 6000

# walk-forward (strictly out-of-sample)
python -m parallax walkforward --csv "C:\PrOxyTradingTerminal\data\NIFTY_5m.csv" --instrument NIFTY --max-bars 2000

# crypto
python -m parallax backtest --csv "C:\PrOxyTradingTerminal\data\crypto_BTCUSD_5m.csv" --instrument BTC

# full replay through the executive (Telegram messages optional)
python -m parallax run --csv "C:\PrOxyTradingTerminal\data\NIFTY_5m.csv" --instrument NIFTY

# test suite
python -m pytest tests -q
```

---

## The Brain — DeepSeek (reasoning) + type-safe engine (authority)

`python -m parallax brain --csv ...NIFTY_5m.csv --instrument NIFTY`

The main brain is a two-layer design, exactly per design doc §18:

* **DeepSeek** (`core/brain/brain.py`) is the *free-text reasoning* validator
  — it synthesizes a thesis and counter-argument, returns a structured JSON
  assessment.
* **TypeSafe System One** (`core/brain/typesafe.py`, `api.typesafe.ai`) is the
  *typed, calibrated judgment* validator — you send a structured state plus
  typed `noul`/choice/score questions and it returns P(true), a chosen label,
  and a score. This is a far better "second opinion" than a second LLM because
  its answers are typed and calibration-aware.

The ensemble (`core/brain/validators.py`) runs the deterministic checks +
DeepSeek + TypeSafe and takes the **most conservative verdict** with the
**minimum confidence** — any validator can reject; none can force a trade.

Credentials live in a gitignored `.env` (never source): `DEEPSEEK_API_KEY`
(`C:\Athena_X\.env`) and `TYPESAFE_API_KEY` (`C:\Parallax\.env`). TypeSafe
defaults to `https://api.typesafe.ai` model `jev-latest` (verified live:
`GET /v1/models` -> `jev-latest`, `jev-preview`).
* **The type-safe engine** remains the *sole authority* for prices, positions,
  risk limits and executable orders. The brain is **advisory/veto-only** — it
  may reject or reduce a candidate, never create, size or send one.

Multi-validator gate (`core/brain/validators.py`): a candidate TRADE must
survive the deterministic confirmation/regime/edge checks *and* the brain. The
brain's confidence is advisory (not calibrated), so it is capped and the
deterministic CalibrationTracker stays the calibration authority.

Persistent memory (`core/memory/persistent.py` + `BrainMemory`): the brain
remembers the entire process — its assessments, outcomes, lessons and regime
history are written to disk and reloaded on startup, so it resumes with context
after any restart. The LLM prompt is built only from structured state + memory
— never credentials or raw broker payloads (§22).

Demo output (real DeepSeek, live): direction `short`, confidence `0.52`,
verdict `reduce` — the brain caught the conflicting EMA alignment and poor
reward-to-risk that a single deterministic signal would have missed.

---

## Why it was net-negative — measured, not argued

`python -m parallax diagnose --csv ...NIFTY_5m.csv --instrument NIFTY`

The diagnosis (cross-checked against `docs/SMCAGENT_FINDINGS.md` and
`docs/BACKTEST_HONESTY.md`) is precise:

1. **The target was the defect, not the entry.** The plan demanded 2–5R, but
   the market offers **0.6R (NIFTY) to 1.7R (BTC)** of favourable excursion
   before the move fades — only ~3% of trades ever reached target.
2. **No profit-locking.** All-or-nothing stop-vs-target means winners
   round-trip back to the stop. The repo already proved lock-profit + trail
   turns a ~31% raw win rate into ~93%.
3. **NIFTY's entry ≈ random.** The measured edge ratio (median MFE / median
   MAE) is **< 1** on NIFTY — the move goes as far against you as for you.
   No exit rule can manufacture an edge where the entry is random.
4. **BTC's entry is real** (MFE ~5R vs MAE ~0.6R), but at ₹10k-equiv capital
   the 1-contract minimum makes friction exceed the risk budget — the
   **cost-to-vol** defect.
5. **Friction ≈ 0.2–0.3R/trade** — index futures are STT-dominated, crypto is
   taker-fee-dominated; both eat the thin edge.

## What profitable traders do — now encoded as deterministic rules

The recurring principles of the most profitable & successful traders (cut
losses fast, lock profit, let winners run, trade with the trend, volatility-
scaled targets, cost-awareness, high selectivity) are now first-class,
type-safe rules:

| Principle | Where encoded |
|---|---|
| Lock profit + trail (the #1 lever) | `core/execution/exits.py` — `ExitManager` |
| Trade only with the HTF trend | `core/decision` — `trend_only` + `min_adx` regime gate |
| Session-local liquidity: sweep the opening range first | `core/perception` + `core/hypotheses` (replaces PDH/PDL) |
| Flatten intraday, never hold overnight | backtest day-boundary flatten |
| Stop-wide-enough-vs-cost gate (reject cost-dominated trades) | `core/risk` — stop-width-vs-friction check |
| Fractional sizing (0.001 BTC steps) | `core/risk` `min_step` + float quantities |
| Correct per-venue costs & point values | `contracts/specs.py` |
| Measure the "why" (MFE/MAE/edge ratio) | `apps/research/diagnostics.py` + `diagnose` CLI |

**The honest verdict:** the opening-range sweep turns NIFTY's entry from
random (edge ratio < 1) to directional (edge ratio > 1), and fractional sizing
plus the stop-width gate let BTC's real edge survive friction.

## Results (strictly out-of-sample walk-forward, fixed strategy)

`python -m parallax walkforward --csv ...NIFTY_5m.csv --instrument NIFTY --train 8000 --test 2000`

| market | OOS trades | win % | net P&L | note |
|---|---|---|---|---|
| **NIFTY** (2y, 15 windows) | **101** | 63% | **+₹45,968** | first net-positive OOS result; ~0.4%/month, modest |
| BTC (5m) | — | — | — | data-limited: both BTC files span ~6 weeks (Jun 20–Aug 1) — fetch 8+ months before validating |

**Caveats (the Aronson checklist, honestly):** the 63% win rate is inflated by
the trailing stop (many small wins); the honest metrics are the profit factor
and net P&L. 101 trades just clears Tharp's 100-trade minimum. The strategy was
**not** re-optimized per window (fixed rules), so this is a fair OOS test.
**Robustness:** the result holds across five exit-config variations
(lock 0.4–0.6, trail 0.4–0.6 — all positive, PF 1.5–15.5), so it is not a
knife-edge. Still to run before promotion: bootstrap significance and a full
parameter sweep. ~0.4%/month is a *tradable positive edge*, not a get-rich lever.

---

## Non-negotiable guarantees implemented

- Every order requires a validated **decision ID** and **risk authorization ID**.
- The risk engine can **veto any trade** and is independent of the reasoning layer.
- **Idempotency keys** block duplicate execution; order intents are schema-validated.
- Malformed messages are rejected at the boundary (§17.1).
- Any material data-quality failure degrades to **WAIT / ABORT**.
- **Kill switch** and pause/resume are independent and always available.
- The **no-trade path** is a first-class, tested output.
- Reflection produces learning records that **never** auto-rewrite live logic.

## Directory layout

    parallax/        the package (contracts, core, executive, adapters, apps, cli)
    tests/           55 pytest tests (contracts, indicators, structure, reasoning,
                     decision, risk, execution, exits, metacognition, end-to-end)
    pyproject.toml
