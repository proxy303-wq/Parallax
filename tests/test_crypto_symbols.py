"""Multi-symbol crypto worker: per-symbol contract specs and per-symbol journal state.

Two crypto workers share one journal store, so the failure modes here are silent ones:

  * the WRONG contract_value keeps P&L accidentally correct (qty x contract_value is
    invariant) while reporting ten times the real contract count -- which is exactly why
    it would survive until a demo/live order went out at the wrong size;
  * a global position wipe on exit erases the OTHER worker's open position, so the
    dashboard shows flat while a trade is live.
"""
from __future__ import annotations

import pytest

from parallax.apps.worker.crypto_smc import CryptoWorker
from parallax.config.crypto import SMCConfig, USD_INR, product
from parallax.core.smc_crypto import Decision, SMCCrypto
from parallax.web.store import JournalStore


# --------------------------------------------------------------- contract specs
def test_product_specs_match_the_venue():
    assert product("BTCUSD").contract_value == 0.001
    assert product("ETHUSD").contract_value == 0.01      # 10x BTC -- the whole trap
    assert product("XAUTUSD").contract_value == 0.001
    assert product("ETHUSD").tick_size == 0.05
    assert product("XAUTUSD").max_notional_usd == 50_000.0
    assert product("XAUTUSD").maker_rate == 0.0001        # 1bp, cheaper than BTC/ETH


def test_unknown_symbol_falls_back_to_btc_and_is_case_insensitive():
    assert product("DOGEUSD").symbol == "BTCUSD"
    assert product("ethusd").symbol == "ETHUSD"


# --------------------------------------------------------------- sizing
def test_sizing_uses_the_symbols_own_contract_value():
    """Same risk, price and stop -> ETH must size 1/10 the contracts of BTC."""
    equity, entry, stop = 800_000.0, 4_000.0, 3_900.0
    btc = SMCCrypto(SMCConfig(symbol="BTCUSD")).size(equity, entry, stop)
    eth = SMCCrypto(SMCConfig(symbol="ETHUSD")).size(equity, entry, stop)
    assert btc > 0 and eth > 0
    assert eth == pytest.approx(btc / 10.0, rel=0.02)


def test_pnl_is_invariant_to_contract_size_but_the_count_is_not():
    """The invariance is why the bug hid; pin BOTH facts so it cannot come back."""
    equity, entry, stop, exit_px = 800_000.0, 4_000.0, 3_900.0, 4_200.0
    got = {}
    for sym in ("BTCUSD", "ETHUSD"):
        m = SMCCrypto(SMCConfig(symbol=sym))
        qty = m.size(equity, entry, stop)
        risk = qty * m.prod.contract_value * abs(entry - stop) * USD_INR
        m.open_position(1, entry, qty, stop, risk)
        got[sym] = (qty, m._exit(Decision(), exit_px, "stop").pnl)

    # P&L agrees: qty x contract_value is the same base exposure either way
    assert got["BTCUSD"][1] == pytest.approx(got["ETHUSD"][1], rel=0.02)
    # the CONTRACT COUNT does not -- this is what the per-symbol spec fixes
    assert got["BTCUSD"][0] == pytest.approx(10 * got["ETHUSD"][0], rel=0.02)


# --------------------------------------------------------------- journal isolation
def test_state_keys_are_per_symbol(tmp_path):
    store = JournalStore(str(tmp_path / "j.db"))
    btc = CryptoWorker(store=store, symbol="BTCUSD")
    eth = CryptoWorker(store=store, symbol="ETHUSD")
    assert btc.state_key == "crypto_state_BTCUSD"
    assert eth.state_key == "crypto_state_ETHUSD"
    assert btc.state_key != eth.state_key
    # the worker must carry the symbol's contract, not the module default
    assert btc.prod.contract_value == 0.001
    assert eth.prod.contract_value == 0.01
    assert eth.cfg.symbol == "ETHUSD"


def test_closing_one_symbol_does_not_clear_the_other(tmp_path):
    store = JournalStore(str(tmp_path / "j.db"))
    store.set_position("BTCUSD", "crypto", "BUY", 100, 60000, 59000, 0)
    store.set_position("ETHUSD", "crypto", "SELL", 50, 4000, 4100, 0)
    assert len(store.positions()) == 2

    store.clear_position("ETHUSD")
    left = {p["instrument"] for p in store.positions()}
    assert left == {"BTCUSD"}, "an ETH exit must not erase the BTC position"
