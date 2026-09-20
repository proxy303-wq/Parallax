# Finding: the live worker does NOT reproduce the validated backtest

Status: **open, unfixed — needs a decision.** Reported while checking the freshly deployed
paper workers for signals.

## What was asked, and the short answer

The paper workers (BTCUSD + ETHUSD) have produced **no signals** — 0 events, 0 trades,
0 orders, 0 positions. That is expected: they had been up ~17 minutes, and the strategy is
1h with a historical rate of ~14 trades/month per symbol (~1 signal per 2 days).

Investigating *why* they were silent surfaced a larger problem.

## The problem

The deployed worker is **not running the strategy that was validated** in
`btc_jas_compare/REPORT.md` §13–17. Running the repo's own equivalence test
(`equiv_inc.py`, 2,000 bars, spliced BTCUSD):

| | |
|---|---|
| Engine (validated backtest) entries | **41** |
| Live machine fills | 33 |
| **Exact matches** | **15** |
| Engine-only | 26 |
| Live-only | 18 |

**Only 15 of 41 validated entries (37%) are reproduced live.** The report itself never claims
equivalence anywhere — and `REPORT.md` contains no equivalence result at all, so this gap is
not a documented trade-off. It is unresolved.

## Two causes

### 1. The swing window is half the size — and a docstring says otherwise

`parallax/core/smc_crypto.py:51` states:

> `"""Causal SMC features (identical to the backtest strat_c.features)."""`

It is not identical. Measured on the same 1,500 bars:

| | backtest `strat_c.features` | live `smc_crypto.features` |
|---|---|---|
| swing detection | `swing_highs_lows(ohlc, swing_length=L)` where **L = 2\*swing_length = 40** | `swing_highs_lows(ohlc, swing_length=swing_length)` = **20** |
| bos/choch shift | `shift(L)` = 40 | `shift(L)` = 40 |

The library doubles this internally, so live detects structure on an **80-bar** internal window
against the backtest's **160-bar**. Consequences, measured:

* `fvg`, `fvg_top`, `fvg_bot` — **100% identical** (computed from raw OHLC, no swings).
* `bos`/`choch` labels differ on **0.7%** of rows, but the **sign never flips** (0 flips):
  the strategy reads `bos==±1 or choch==±1` for bias, so a BOS/CHoCH *relabel* is harmless.
* The live path still fires **15 structure events vs the backtest's 10** — a different number
  of bias updates, which does change signals.

### 2. Every decision is 60 bars late — and CONFIRM=60 turns out to be load-bearing

`step()` consumes a row only once `CONFIRM` (60) further bars have arrived. Measured over
1,400 bars on both symbols: the lag is **exactly 60 bars (60 h) for every single decision**
(min = median = max = 60), and `bootstrap()` sets `bar_count = len(bars) - 1`, so the first
60 hours of bars after a restart are skipped outright.

The measured *causal* minimum (the smallest n at which a feature row stops changing as the
array grows) is far lower — **1 bar for BTCUSD, 11 for ETHUSD** — so CONFIRM is 5–60x more
delay than causality requires.

**But CONFIRM cannot simply be lowered.** Patched experiments:

| variant | engine | live | exact match |
|---|---|---|---|
| as deployed | 41 | 33 | 15 |
| features patched to match backtest, CONFIRM=60 | 41 | 23 | 12 |
| ... patched, CONFIRM=41 | 41 | 4 | 2 |
| ... patched, CONFIRM=21 | 41 | 4 | 2 |
| ... patched, CONFIRM=11 | 41 | **0** | **0** |

Lowering CONFIRM drives signals to **zero**. This is the sliding-window instability the module
docstring already warns about: `step()` computes features on a 400-bar window that *slides*, so
its swing marks chase the window edge and are never final. CONFIRM=60 is not a causality
margin; it is what makes the sliding window's marks readable at all.

Fixing the swing window alone therefore does not help (12/41). **Both** causes have to be fixed
together, and the second one requires structural change.

## Why it matters

The paper workers are measuring an **unvalidated variant**, not the edge in the report. Any
positive or negative paper result — and the earlier BTC live/paper history — reflects that
variant. Nothing about the +173.51% BTC / +124.68% ETH walk-forward is contradicted; those
stand. But the claim "the live worker runs the validated strategy" is not currently true.

This affects the **pre-existing BTC worker too**, not just the new ETH one.

## Fix options

**A. Compute features over the whole expanding history (recommended).** Keep the full bar
series in the machine and run `features()` on it, consuming row j only once it is final
(measured need: ~11 bars; use ~21 for margin). Marks are then anchored to a growing array, not
a sliding window, so they stabilise — this is what makes the backtest's marks final. Cost: a
full-history `features()` call per tick instead of a 400-row one (~1-2 s on 23k bars, once an
hour; acceptable). `bootstrap()` must also consume the last CONFIRM bars instead of skipping
them.

**B. Ship the frozen config as a deterministic replay instead.** The simplest way to guarantee
"live == backtest" is to run the backtest's own `signals_limit` generator over the full history
each tick and act on the most recent not-yet-acted bar. Slower, but it removes the twin
entirely.

**C. Do nothing, and re-label it.** Accept that live is a different strategy — but then it must
be re-validated on its own terms before it can be trusted, and the report should say so.

In all cases the acceptance gate should be explicit: **entry match vs `equiv_inc.py` >= ~95%
before promoting past paper.** Today it is 37%.

## Reproducing

```powershell
python <btc_jas_compare>\src\equiv_inc.py                       # 15/41 -- the repo's own test
python research\eth_xaut_compare\src\live_lag_check.py          # lag is exactly 60 bars
python research\eth_xaut_compare\src\feature_parity.py          # FVG identical, bos/choch labels differ
python research\eth_xaut_compare\src\equivalence_fix_test.py    # patched variants, incl. CONFIRM sweep
```
