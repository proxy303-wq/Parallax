# ETH and gold (XAUT) — the finalized BTC strategy, re-run out of sample

**Question:** the crypto strategy finalized in `btc_jas_compare` (REPORT.md §12–17) was validated
on BTCUSD only. Does it hold on **ETH** and on **XAUT** (gold), 2024 → Sep 2026?

**Answer in one line:** ETH **yes** (+124.68% compounded, t = 2.16, positive in all three
calendar years, and it made that while ETH *fell* 14%). Gold **also works but is a different
animal** — highest per-trade edge of the three (+0.412R), lowest total return (+41.68%, because
far fewer signals), and it did **not** beat simply holding gold. **XAUT itself cannot be tested
over the requested window: no XAUT series anywhere has history before 2026-03-26** — Binance
listed the pair on 2026-03-26, Delta India on 2026-04-17, and Delta Global never listed it. Gold
over the full window was therefore tested on PAXG (validated against both XAUT series at
r ≥ 0.99970), and XAUT directly over the 5.1–5.8 months that exist.

---

## 1. Methodology — what was and was not changed

The strategy configuration is **frozen**. Nothing was re-optimized, re-tuned, or re-selected on
the new symbols. This is a faithful port of the finalized walk-forward script
(`btc_jas_compare/src/run_refine_wfo3.py`), which produced the recorded headline (REPORT.md §13.4).

| Element | Value (unchanged) |
|---|---|
| Features | `strat_c.features(bars, 20)` — causal SMC, BOS/CHoCH + FVG, lagged 2×swing |
| Entry | `signals_limit(zone="fvg")` — resting limit at the FVG edge, ttl 20 bars |
| Config grid | stop floor {1.5, 2.0, 3.0} × exit {R3, R5, trail5, R10+trail5} × {long, long+short} |
| Selection | best **in-sample total return**, requiring ≥ 30 in-sample trades |
| Folds | monthly, expanding in-sample, OOS = the next calendar month |
| Costs | maker 2.36bp entry, taker 5.90bp stop, maker target, 1bp slippage, funding 0.01%/8h, 18% GST |
| Risk | 0.75% of equity per trade, 1× (no leverage), capital Rs 5,00,000 |

### 1.1 Harness parity — verified, not assumed

The recorded BTC result was +170.40% / 427 trades. Re-running **the unmodified original script**
on today's data gives **+173.51% / 451 trades**. My port reproduces that fold-for-fold
(identical configuration picks: floor 1.5 in all 30 folds; exit R10+trail5 14, trail5 14, R3 1, R5 1).

The ~1.8% drift is a **data-snapshot** difference, not a logic difference: the research folder's
`data/delta` and `out` artifacts were re-materialized at 12:00:22 on 2026-09-20, after the runs
behind the written report. **Every BTC number below is therefore the figure reproducible today**,
and all three books are compared on the identical harness and cost model.

---

## 2. Data provenance — including one hard limit

| Book | Source | Bars | Coverage | Missing slots | Zero-volume | Malformed |
|---|---|---|---|---|---|---|
| BTC | Delta India `BTCUSD` 1h | 23,600 | 2024-01-10 → 2026-09-19 | 0 | 2.4% | 0 |
| ETH | Delta India `ETHUSD` 1h | 22,967 | 2024-02-06 → 2026-09-20 | 0 | 0.9% | 0 |
| XAUT | Delta India `XAUTUSD` 1h | **3,740** | **2026-04-17** → 2026-09-20 | 0 | 0.0% | 0 |
| XAUT | Binance `XAUTUSDT` 1h | **4,258** | **2026-03-26** → 2026-09-19 | 0 | 0.0% | 0 |
| Gold proxy | Binance `PAXGUSDT` 1h | 23,832 | 2024-01-01 → 2026-09-19 | 0 | 0.0% | 0 |

### 2.1 XAUT does not have 2024–2026 history — anywhere

Probed month by month in bounded windows (a wide request is *not* an error: the endpoint
silently returns only the most recent ~4,000 bars, so coverage can never be inferred from a
wide query):

* **Delta India `XAUTUSD`**: empty for every month before 2026-04; first bar **2026-04-17**.
* **Delta Global `XAUTUSD`**: empty for **every** month probed — the product does not exist there.
* **Binance `XAUTUSDT`**: first hourly bar **2026-03-26 14:00** (the whole XAUT pair
  family — `XAUTUSDT`, `XAUTUSDC`, `XAUTBTC`, `XAUTU` — listed the same day). Archive
  months present: 2026-03 … 2026-08.

**Binance's XAUT listing is 3 weeks older than Delta's** (2026-03-26 vs 2026-04-17), so the
Binance series is used to *extend* the direct XAUT window from 5.1 to **5.8 months** and to
cross-check Delta's prices. It does not solve the 2024 problem: **no XAUT series anywhere has
hourly history before 2026-03-26.**

So the requested 2024 → Sep 2026 XAUT backtest is **impossible on XAUT's own data**. Reporting a
number for it would require inventing history. Two honest substitutes are given:
a validated PAXG proxy over the full window (§3.3), and a direct XAUT run over the 5.1 months
that actually exist (§4).

### 2.2 The gold proxy is validated, not assumed

PAXG (PAX Gold) and XAUT (Tether Gold) are both 1-troy-ounce tokenized gold claims, so PAXG is
the natural stand-in. That claim is tested rather than asserted — on the 3,732 hourly bars where
both exist:

| Check | Result |
|---|---|
| Correlation | **0.99972489** |
| Mean basis (PAXG / XAUT − 1) | **+0.072%** |
| Median basis | +0.073% |
| Mean absolute price difference | **$5.10** |

That is the same order of agreement as the report's own Delta-vs-Binance BTC cross-check
(0.99999949), so the proxy is admissible.

### 2.2b Three-way check, now including Binance's own XAUT

With the Binance XAUT series fetched, all three gold price series can be compared directly on
their 3,732-bar common overlap:

| Pair | Correlation | Mean basis | Mean abs. price diff |
|---|---|---|---|
| XAUT-Binance vs XAUT-Delta | **0.99991702** | −0.056% | $3.63 |
| PAXG-Binance vs XAUT-Binance | 0.99970120 | +0.128% | $6.28 |
| PAXG-Binance vs XAUT-Delta | 0.99972489 | +0.072% | $5.10 |

Maximum Binance-vs-Delta XAUT divergence across the overlap is **$8.87** on a ~$4,400 price
(0.20%). All three series are the same gold market, so the PAXG proxy stands and the Delta XAUT
prices are independently corroborated.

### 2.3 A data hazard that was caught

Binance Vision switched `open_time` units mid-archive (**milliseconds** before ~2025,
**microseconds** after). Choosing one unit globally moved half the sample to the year **56971**.
The fetcher now detects the unit **per file** from that file's own magnitude; the successful run
reports `units=['ms','us']`, 0 missing slots and 0 zero-volume bars.

---

## 3. Walk-forward results (Rs 5,00,000, 0.75% risk, 1×, Delta costs)

| Book | OOS span | Folds | Compounded | Final (Rs) | Max DD | Sharpe | Positive months | Trades | Fill rate | OOS expectancy | t | 95% CI |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **BTC** (Delta) | Apr 24 – Sep 26 | 30 | **+173.51%** | 13,67,533 | −14.62% | 2.02 | 23/30 | 451 | 81.3% | **+0.380R** | **2.94** | [+0.13, +0.63] |
| **ETH** (Delta) | May 24 – Sep 26 | 29 | **+124.68%** | 11,23,401 | −13.81% | 1.61 | 17/29 | 403 | 80.4% | **+0.321R** | **2.16** | [+0.03, +0.61] |
| **GOLD** (PAXG/Binance) | Jun 24 – Sep 26 | 28 | **+41.68%** | 7,08,384 | −8.88% | 1.25 | 17/28 | 254 | 83.1% | **+0.412R** | **2.60** | [+0.10, +0.72] |

Both new books clear the same bar the BTC result did: **pooled out-of-sample expectancy is
positive and the 95% confidence interval excludes zero.**

### 3.1 Year by year, out of sample

| Year | BTC book | ETH book | Gold book | BTC | ETH | Gold |
|---|---|---|---|---|---|---|
| 2024 (part) | **+41.85%** | **+35.87%** | **+4.82%** | +32.48% | +12.08% | +13.00% |
| 2025 | **+31.58%** | **+8.07%** | **+12.89%** | −6.97% | **−11.41%** | **+64.83%** |
| 2026 (to Sep) | **+46.54%** | **+53.01%** | **+19.72%** | −7.19% | **−13.60%** | +0.57% |

**Positive in all three calendar years on all three assets.** The ETH result is the striking one:
the book made **+124.68% while ETH fell 14.21%**, and in 2025 and 2026 specifically it made +8%
and +53% while ETH fell 11% and 14%. That is the same non-correlation the BTC study found, now
reproduced on a second asset.

### 3.2 What the walk-forward chose on each asset

| Asset | Stop floor | Exit selected | Direction |
|---|---|---|---|
| BTC | 1.5 ATR in **30/30** folds | R10+trail5 14, trail5 14, R3 1, R5 1 | long+short 30/30 |
| ETH | 1.5 in 12, **2.0 in 17** | **trail5 26**, R10+trail5 3 | long+short 27, long 2 |
| Gold | 1.5 in 17, 2.0 in 11 | **R5 11, R10+trail5 11, R3 6** | **long 27**, long+short 1 |

The selection adapted per asset rather than collapsing to one setting — evidence the procedure
is responding to the data, not memorising BTC.

### 3.3 Gold is a different beast — and does not beat buy-and-hold gold

Gold is the only book with a **negative result worth stating plainly**:

* **Highest edge per trade** (+0.412R, the best of the three) yet the **lowest total return**
  (+41.68%). Fewer signals (254 vs 451) and the walk-forward mostly chose *fixed-R* exits
  (R3/R5 in 17 of 28 folds) instead of the trail — gold's moves are slower and steadier, so a
  5×ATR chandelier gives back more of the trend.
* **Buy-and-hold gold returned +87.31% over the same span.** The book captured roughly half of
  that with **one third of the drawdown** (−8.88% vs −29.23%). So on gold this is a
  **risk-reduction** tool, not an alpha source — it is long-biased there (27/28 folds chose long),
  which is what you would expect from an asset in a strong secular uptrend.

---

## 4. The only direct XAUT evidence: the frozen configuration, 17 Apr – 20 Sep 2026

XAUT's 5.1 months cannot support monthly walk-forward folds, so the **frozen** configuration
(1.5 ATR floor, 5 ATR trail, long+short — exactly what BTC's walk-forward settled on) was applied
unchanged. The same run is shown for all four books on the identical window, which makes this the
cleanest apples-to-apples comparison in the report.

| Book | Trades | Return | Max DD | Expectancy | t | Fill rate | Underlying |
|---|---|---|---|---|---|---|---|
| **XAUT** (Delta) | 67 | **+12.08%** | −6.38% | +0.265R | 0.69 | 76.1% | **−9.21%** |
| **GOLD** (PAXG) | 75 | **+12.32%** | −5.39% | +0.392R | 0.99 | 80.6% | **−9.59%** |
| **ETH** (Delta) | 68 | **+22.51%** | −14.94% | +0.601R | 0.88 | 76.4% | +8.58% |
| **BTC** (Delta) | 74 | **+21.30%** | −10.83% | +0.603R | 1.05 | 87.1% | +6.92% |

### 4.1 Extended XAUT window (5.8 months, using the Binance listing)

Using Binance's earlier XAUT listing lengthens the direct XAUT test. All three gold books run
over the identical 2026-03-26 → 2026-09-19 window:

| Series | Trades | Return | Max DD | Expectancy | t | Fill rate | Underlying |
|---|---|---|---|---|---|---|---|
| **XAUT** (Binance) | 87 | **+9.61%** | −5.83% | +0.097R | 0.30 | 77.0% | −2.02% |
| **PAXG** (Binance) | 85 | **+11.51%** | −5.39% | +0.340R | 0.97 | 80.2% | −2.32% |

Both gold books are profitable over both windows while gold itself was flat-to-down, and the two
independent XAUT/PAXG series land within ~2 points of each other. **But every gold t-statistic
sits between 0.30 and 0.99 — none of this is statistically significant at 5.8 months.**

Two things this establishes:

1. **XAUT and PAXG produce almost identical results** (+12.08% vs +12.32%) on the same window —
   validating the proxy at the *strategy* level, not just the price level.
2. **XAUT made money while gold fell 9.21%**, in line with BTC and ETH.

**But be honest about the sample: n = 67, t = 0.69, 95% CI [−0.49R, +1.02R] — it includes zero.**
Five months is not evidence of an edge; it is merely consistent with one.

---

## 5. Limitations, stated plainly

1. **XAUT has no 2024–2026 history.** The requested backtest was run on PAXG as a validated
   proxy (r = 0.99972); XAUT itself is only testable over 5.1 months.
2. **The BTC reference drifted.** Recorded +170.40% / 427 trades vs +173.51% / 451 reproducible
   today, because the research data artifacts were re-materialized after the report was written.
   All three books here share one harness, so the *comparison* is sound.
3. **The 80% limit-fill rate is a backtest assumption**, not a measurement. This is where
   backtest-to-live decay lives, exactly as the BTC report warned.
4. **Fold spans differ slightly** (BTC 30 folds from Apr 2024, ETH 29 from May, gold 28 from Jun)
   because each book needs enough in-sample history. The §4 frozen comparison removes that
   difference for the recent window.
5. **The gold book is long-biased and underperformed buy-and-hold gold** over the period. It is
   not a gold-alpha strategy.
6. **Configurations were explored on BTC first.** The walk-forward validates the *selection
   procedure* on the new symbols, not that the parameter values are optimal for them.
7. **No funding-rate variation** (flat 0.01%/8h) and **no market-impact model**.
8. Individual 5-month statistics are **not significant** anywhere; only the multi-year pooled
   samples are.

## 6. Verdict

| Claim | Verdict |
|---|---|
| The strategy generalizes to **ETH** | **Supported.** +124.68%, t = 2.16, CI excludes zero, positive in all 3 years, and it made that while ETH fell 14%. |
| The strategy generalizes to **gold** | **Partially.** Positive in all 3 years and the highest per-trade edge, but +41.68% versus +87.31% for simply holding gold — a drawdown-reduction tool, not alpha. |
| The strategy works on **XAUT specifically** | **Untestable over 2024–2026.** No XAUT series exists before 2026-03-26 anywhere (checked Binance, Binance Vision, Delta India, Delta Global; Binance listed the whole XAUT family on 2026-03-26). 5.1–5.8 months of direct evidence agree with the PAXG proxy and are profitable, but t ≤ 0.69 and the CI includes zero. |

**The honest headline: ETH is a genuine second confirmation of the edge. Gold works but you
would have done better just owning gold. XAUT cannot yet be judged.**

---

## 7. Leverage: helps gold, does nothing for ETH

The live worker (`parallax/config/crypto.py`) runs with `max_leverage = 5.0`, while the
validated backtest used 1×. Re-running the identical walk-forward at 5× isolates what leverage
actually does — and the answer differs by asset:

| Book | 1× compounded | 5× compounded | 1× max DD | 5× max DD | 1× Sharpe | 5× Sharpe |
|---|---|---|---|---|---|---|
| ETH | +124.68% | **+123.14%** | −13.81% | −13.94% | 1.61 | **1.48** |
| Gold (PAXG) | +41.68% | **+80.97%** | −8.88% | −14.89% | 1.25 | 1.37 |

This reproduces the mechanism the BTC report identified: **leverage does not multiply returns, it
only removes the 1× notional cap — and it helps exactly when that cap was binding.**

* On **gold** the cap *was* binding, so 5× roughly doubles the return (+41.68% → +80.97%) at the
  cost of a larger drawdown (−8.88% → −14.89%).
* On **ETH** the cap was essentially not binding, so 5× changes nothing (+124.68% → +123.14%) and
  slightly *degrades* risk-adjusted return.

**Practical consequence for the live worker: the 5× setting is doing real work on gold and
nothing on ETH.** If the worker is pointed at ETH, 5× is risk without reward; if pointed at gold,
it is the difference between a mediocre and a decent result.

---

## 8. What was changed in the live system (paper deployment)

On the strength of the ETH result, ETH was added to the **paper** deployment. That required
three fixes, because the worker assumed BTC everywhere:

| Fix | Why it was needed |
|---|---|
| `config.crypto.product(symbol)` per-symbol spec | ETHUSD is a **0.01** contract vs BTCUSD's 0.001. Sizing ETH with the BTC constant reports **10x** the real contract count -- and the P&L stays *accidentally* correct (qty x contract_value is invariant), so it would have hidden until a demo/live order went out at ten times the intended size. XAUTUSD also differs: 1bp maker/taker, $50k cap, 0.5% maintenance. |
| `crypto_state_<SYMBOL>` key + `JournalStore.clear_position` | Two workers share one journal store. A single `crypto_state` key would have the two books overwrite each other on the dashboard, and the old global `clear_positions()` call would erase the *other* symbol's live position on any exit. |
| `--symbol` CLI flag on the worker | One code path, one instance per market; `deploy/parallax-crypto-eth.service` is the second unit. |

**Shared-state caveats, now documented in `deploy/README.md`:** the trade mode
(`settings.mode`) is **global**, so flipping the dashboard to demo or live moves *both* crypto
workers; and the paper capital is shared, so the combined book risks ~2 x 0.75% per cycle.

Verification for these changes: `tests/test_crypto_symbols.py` (6 tests) pins the contract
specs, the sizing ratio, the P&L invariance, the per-symbol state keys and the position
isolation. Full suite: **94 passed**.

---

### Reproducing this report

```powershell
cd C:\PrOxyTradingTerminal\.research\eth_xaut_compare

python src\fetch_symbols.py                                  # Delta 1h for ETH/XAUT
python src\fetch_one.py XAUTUSD india 1h
python src\fetch_binance_gold.py                              # PAXG from Binance Vision (2024+)
python src\fetch_binance_xaut.py                              # Binance XAUTUSDT (2026-03-26+)
python src\probe_binance_xaut.py                              # listing-date evidence
python src\basis_check.py                                     # PAXG-vs-XAUT validation
python src\gold_crosscheck.py                                 # three-way gold cross-check

python src\run_wfo.py data\ETHUSD_india_1h.parquet  ETH-DELTA  --out out\folds_ETH.csv
python src\run_wfo.py data\PAXGUSDT_binance_1h.parquet GOLD-PAXG --out out\folds_PAXG.csv
python src\run_wfo.py "C:\PrOxyTradingTerminal\.research\btc_jas_compare\data\delta\BTCUSD_1h.parquet" BTC-DELTA --out out\folds_BTC.csv

python src\verify_original.py                                # unmodified BTC script = parity control
python src\run_frozen.py data\XAUTUSD_india_1h.parquet "XAUT" --since 2026-04-17T12:00
```

| File | What it is |
|---|---|
| `src/run_wfo.py` | faithful port of the finalized walk-forward, parameterised by price series |
| `src/run_frozen.py` | the frozen final config over one window (no selection) |
| `src/verify_original.py` | the original script, unmodified, as the parity control |
| `src/fetch_symbols.py`, `src/fetch_one.py` | Delta Exchange candle fetchers (backward pagination) |
| `src/fetch_binance_gold.py` | Binance Vision gold fetcher with per-file ms/us handling |
| `src/fetch_binance_xaut.py` | Binance XAUTUSDT fetcher (the 2026-03-26 listing) |
| `src/probe_binance_xaut.py` | evidence for the XAUT listing date, across every pair |
| `src/basis_check.py` | PAXG-vs-XAUT proxy validation |
| `src/gold_crosscheck.py` | three-way XAUT/PAXG price cross-check |
| `src/vps_status.py` | read-only VPS deployment status |
| `src/vps_deploy.py` | deploy + verify both crypto workers in paper mode |
| `src/smoke_worker.py` | hermetic worker smoke test (throwaway db, stubbed Telegram) |
| `src/check_dashboard.py` | asserts /crypto renders one correctly-labelled card per symbol |
| `out/folds_*.csv` | per-fold walk-forward records |
