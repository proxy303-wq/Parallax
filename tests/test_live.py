"""Live deployment config + sizing tests (offline)."""
from parallax.config.live import dhan_futures_risk_config, lots_from_capital


def test_lots_from_capital_3_lots_at_750k():
    # 1% risk of Rs7.5L = Rs7,500 / (38pts x Rs65) = 3 lots
    assert lots_from_capital(750_000, 38, risk_pct=0.01, max_lots=3) == 3


def test_lots_from_capital_half_percent_is_one_lot():
    assert lots_from_capital(750_000, 38, risk_pct=0.005, max_lots=3) == 1


def test_lots_from_capital_capped_at_max():
    # even huge capital is capped at 3 lots
    assert lots_from_capital(5_000_000, 38, risk_pct=0.01, max_lots=3) == 3


def test_lots_from_capital_zero_when_unfunded():
    assert lots_from_capital(63.0, 38, risk_pct=0.01, max_lots=3) == 0


def test_dhan_futures_config():
    cfg = dhan_futures_risk_config()
    assert cfg.max_position_size == 3
    assert cfg.point_value == 65
    assert cfg.min_step == 1
    assert cfg.max_risk_per_trade_pct == 0.01
