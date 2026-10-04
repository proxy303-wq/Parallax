from parallax.apps.ops.commodity_collar import collar_strikes, payoff

def test_strikes_snap_to_grid():
    p, c = collar_strikes(100.0, [80, 90, 95, 100, 105, 110, 120], 5.0, 5.0)
    assert p == 95.0 and c == 105.0


def test_strikes_none_when_grid_too_narrow():
    p, c = collar_strikes(100.0, [100], 10.0, 10.0)
    assert p is None and c is None


def test_payoff_floors_and_caps():
    # F=100, put=90 (p=2), call=110 (c=2) -> net 0, floor -10, cap +10
    assert abs(payoff(100.0, 90.0, 110.0, 0.0, 80.0) - (-10.0)) < 1e-9
    assert abs(payoff(100.0, 90.0, 110.0, 0.0, 120.0) - 10.0) < 1e-9
    assert abs(payoff(100.0, 90.0, 110.0, 0.0, 100.0) - 0.0) < 1e-9


def test_payoff_accounts_for_net_cost():
    # net debit of 3 shifts everything down by 3
    assert abs(payoff(100.0, 90.0, 110.0, 3.0, 80.0) - (-13.0)) < 1e-9
    assert abs(payoff(100.0, 90.0, 110.0, 3.0, 120.0) - 7.0) < 1e-9
