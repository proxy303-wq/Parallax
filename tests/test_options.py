"""Index-options module tests — offline (pricing/Greeks/engine/contract resolution)."""
from parallax.core.options import ChainContext, OptionsEngine, OptionsConfig
from parallax.core.options import optmath as om
from parallax.adapters.market_data.dhan_options import resolve_contract


def test_bs_atm_and_iv_roundtrip():
    S, K, T, sig = 23398.0, 23400.0, 7 / 365.0, 0.13
    price = om.bs_price(S, K, T, sig, "c")
    assert 100.0 < price < 300.0
    iv = om.implied_vol(price, S, K, T, "c")
    assert iv is not None and abs(iv - sig) < 1e-3


def test_delta_strike_put_below_spot():
    k = om.delta_strike(23398.0, 0.13, 7, 0.16, "put")
    assert k is not None and k < 23398.0


def test_sell_iron_condor_collects_theta():
    eng = OptionsEngine(OptionsConfig())
    ctx = ChainContext(spot=23398, sigma=0.13, dte=7, strike_step=50,
                       expiry="2026-09-29", symbol="NIFTY", lot_size=65)
    s = eng.sell_strangle(ctx)
    assert s.net_premium > 0          # credit
    assert s.net_theta_day > 0        # short premium collects theta
    assert abs(s.net_delta) < 20      # near delta-neutral
    assert s.max_loss > s.max_profit  # defined risk > credit
    assert 0.5 < s.prob_profit < 0.95
    assert len(s.break_even) == 2


def test_buy_directional_is_debit():
    eng = OptionsEngine(OptionsConfig())
    ctx = ChainContext(spot=23398, sigma=0.13, dte=7, strike_step=50,
                       expiry="2026-09-29", symbol="NIFTY", lot_size=65)
    b = eng.buy_directional(ctx, "bearish")
    assert b.net_premium < 0          # debit
    assert abs(b.max_loss - (-b.net_premium)) < 1e-6
    assert b.net_delta < 0            # long put = short delta


def test_resolve_nifty_and_finnifty_contracts():
    n = resolve_contract("NIFTY", 23400, "CE", "2026-09-29")
    assert n is not None
    assert n.lot_size == 65
    assert n.trading_symbol.startswith("NIFTY-")
    assert n.security_id > 0
    f = resolve_contract("FINNIFTY", 23400, "CE", "2026-09-29")
    assert f is not None
    assert f.lot_size == 60
    assert f.trading_symbol.startswith("FINNIFTY-")
