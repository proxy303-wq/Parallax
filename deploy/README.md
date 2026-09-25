# Deploying PARALLAX to the VPS

## Services

| Unit | What it runs | Notes |
|---|---|---|
| `parallax-web` | FastAPI dashboard | journal store is the single source of truth |
| `parallax-crypto` | SMC crypto worker, **BTCUSD** | `--symbol` defaults to BTCUSD |
| `parallax-crypto-eth` | SMC crypto worker, **ETHUSD** | second instance, same code |
| `parallax-live` | futures ICT runner + options orchestration | |
| `parallax-opt-nifty` | NIFTY expiry-day condor | plain Tuesday |
| `parallax-opt-banknifty` | BANKNIFTY expiry-day condor | last Tuesday of the month |
| `parallax-opt-sensex` | SENSEX expiry-day condor | plain Thursday |
| `parallax-opt-bankex` | BANKEX expiry-day condor | last Thursday of the month |
| `parallax-chat` | Telegram chat bot | |

All four options units run continuously, but `config.schedule.options_plan()` names
**exactly one** index per day, so at most one of them ever has work. Each holds a
position to expiry; `options_hold_<INDEX>.json` is its live state, and deleting that
file makes it forget an open position and never exit it.

## Per-index condor settings (they are NOT the same, on purpose)

A strike COUNT is a different bet on every index because one strike is worth a
different fraction of price and of a day's move.  Three strikes is 0.64% of spot
on NIFTY (50-pt step, ~23,400) but only 0.40% on SENSEX (100-pt step, ~74,200),
so the identical config was a third as much room in volatility terms on the BSE
indices.  Measured from the rolling-option ladder, net of 3% cost, close-confirmed:

Chosen by STRICT walk-forward (selector sees only completed quarters), 7 lots,
valued at INTRINSIC settlement so no shape is penalised by ladder truncation:

| Unit | `--short-off` | `--enter-at` | return/DD at 3 / 4 / 5 | choice |
|---|---|---|---|---|
| `parallax-opt-nifty` | **5** | 09:20 | 7.2x / 9.3x / **24.2x** | wider buys 2.6x better risk for 12% less total |
| `parallax-opt-sensex` | **5** | **09:30** | 7.6x / 9.3x / **14.3x** | same direction; 09:30 still beats 09:20 at short_off 5 (DD 47,290 vs 110,240) |
| `parallax-opt-bankex` | 3 (default) | 09:20 | 1.9x / 1.3x / 0.6x | the ONLY index where tighter is better on both total and risk |
| `parallax-opt-banknifty` | 3 (default) | 09:20 | 4.2x / **5.0x** / 4.9x | 19 expiries; 4 is marginally better but inside the noise |

Bankex and Banknifty sit on the CLI default rather than a tuned value, and that is
deliberate: Bankex has 9 usable expiries and Banknifty 19, against Nifty's 83 and
Sensex's 79.  Do not "fix" them from a sample that small.

**Do not tune `--short-off` over time.**  A walk-forward selector that picks each
quarter's shape from prior quarters' results never beat the best fixed choice: on
Nifty it finished below BOTH fixed 3 and fixed 4, and on Sensex and Banknifty it
locked onto 3 and finished exactly level.  Pick one and leave it.

Wing stays 3 everywhere.  Two things to know before "improving" these:

* **The ladder is the ceiling.** Dhan's rolling endpoint stops at ±10 strikes, and
  a leg's offset is measured from each bar's own ATM — so once the index trends,
  far legs leave the ladder and the expiry can no longer be valued.  SENSEX keeps
  51 of 79 expiries at short_off 4 but only 36 of 79 at 5.  Wider is not free,
  and `_run_sigma`'s `require_close` drops the trades it cannot score rather
  than scoring them on a stale mark (which is what once made every BANKEX expiry
  look like a full-credit win).
* **Selection is not neutral.** The trades that drop out are disproportionately
  the trend days, which are exactly the days a wide shape needs to be judged on.
  So a wide shape is only ever measured on the days it survived.

Install:

```bash
cd /opt/parallax
git pull --ff-only
cp deploy/parallax-crypto.service     /etc/systemd/system/
cp deploy/parallax-crypto-eth.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now parallax-crypto parallax-crypto-eth
```

## What is SHARED between the two crypto workers

Adding a second symbol is not free isolation. Read this before enabling it:

* **Trade mode is global.** `settings.mode` is one value for the whole system, so flipping
  the dashboard to `demo` or `live` moves **both** crypto workers at once. There is no
  per-symbol mode. Paper is the safe default.
* **Capital is shared.** Both workers read the same `paper_capital`, so each risks 0.75%
  of the same balance — the combined book risks roughly **2 x 0.75% per cycle**. Treat the
  aggregate as one portfolio when judging drawdown.
* **The journal db is shared.** Positions and trades are keyed by instrument, and an exit
  clears only its own row (`JournalStore.clear_position`). Never reintroduce a global
  `clear_positions()` call in a worker: it erases the other symbol's live position.
* **Dashboard state is per symbol** (`crypto_state_<SYMBOL>`), so the two cards never
  overwrite each other. `/crypto` renders every symbol in `app.CRYPTO_SYMBOLS`.

## Contract specs are per symbol — do not assume BTC's

From `GET /v2/products/<symbol>` (read 2026-09-20):

| Symbol | contract_value | tick | maker/taker | notional cap |
|---|---|---|---|---|
| BTCUSD | 0.001 | 0.5 | 2.36 / 5.90 bp (incl. GST) | $100,000 |
| ETHUSD | **0.01** | 0.05 | 2.36 / 5.90 bp (incl. GST) | $100,000 |
| XAUTUSD | 0.001 | 0.01 | **1.0 / 1.0 bp** | $50,000 |

ETHUSD is a **10x larger contract** than BTCUSD. Sizing ETH with the BTC constant reports ten
times the real contract count; the P&L stays accidentally correct because
`qty x contract_value` is invariant, which is why that bug would only surface as a
wrong-sized demo/live order. `config.crypto.product(symbol)` is now the only source of
these numbers — `tests/test_crypto_symbols.py` pins both the invariance and the count.

## Operating

```bash
systemctl status parallax-crypto parallax-crypto-eth
journalctl -u parallax-crypto-eth -f
```

Read the worker's published state without touching the dashboard:

```bash
cd /opt/parallax && .venv/bin/python -c "
import sqlite3, json
r = {k: v for k, v in sqlite3.connect('parallax.db').execute('select key, value from settings')}
print('mode:', r.get('mode'))
for s in ('BTCUSD', 'ETHUSD'):
    st = json.loads(r.get('crypto_state_' + s) or '{}')
    print(s, '| bias', st.get('bias'), '| bars', st.get('bars_processed'),
          '| last_bar', st.get('last_bar'), '| position', bool(st.get('position')))
"
```

## VPS access for the research scripts

`research/eth_xaut_compare/src/vps_status.py` and `vps_deploy.py` read credentials from
the environment — **never hardcode them**:

```powershell
$env:VPS_HOST = "<ip>"
$env:VPS_PW   = "<password>"
python research\eth_xaut_compare\src\vps_status.py
```
