# Deploying PARALLAX to the VPS

> Running on Kuberns instead of a VPS? See `deploy/kuberns.md` - same six processes,
> declared in the root `Procfile` rather than as systemd units.

## Services

| Unit | What it runs | Notes |
|---|---|---|
| `parallax-web` | FastAPI dashboard | journal store is the single source of truth |
| `parallax-crypto` | SMC crypto worker, **BTCUSD** | `--symbol` defaults to BTCUSD |
| `parallax-crypto-eth` | SMC crypto worker, **ETHUSD** | second instance, same code |
| `parallax-live` | NIFTY futures ICT runner | |
| `parallax-chat` | Telegram chat bot | |

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
