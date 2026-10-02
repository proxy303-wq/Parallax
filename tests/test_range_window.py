from parallax.apps.ops.range_window import (ewma, predicted_range, short_off_strikes,
                                          strikes, window_half)

def test_ewma_weights_recent_days_heavier():
    v = ewma([100.0, 100.0, 100.0, 200.0])
    assert 110.0 < v < 130.0        # moves toward 200, not the plain mean 125


def test_ewma_empty_is_zero():
    assert ewma([]) == 0.0


def test_window_is_range_plus_buffer():
    assert window_half(127.0, 2, 50.0) == 227.0
    assert window_half(127.0, 1, 50.0) == 177.0


def test_snaps_to_nearest_strike():
    assert short_off_strikes(227.0, 50.0) == 5   # 250
    assert short_off_strikes(177.0, 50.0) == 4   # 200


def test_full_strike_map_nifty_buffer_two():
    s = strikes([130.0] * 20, atm=22400.0, step=50.0, buffer_strikes=2)
    assert s["predicted_range"] == 130.0
    assert s["short_off"] == 5                    # 130 + 100 = 230 -> 250
    assert s["short_call"] == 22650.0 and s["short_put"] == 22150.0
    assert s["long_call"] == 22800.0 and s["long_put"] == 22000.0


def test_full_strike_map_sensex_buffer_two():
    s = strikes([420.0] * 20, atm=72400.0, step=100.0, buffer_strikes=2)
    assert s["short_off"] == 6                    # 420 + 200 = 620 -> 600
    assert s["short_call"] == 73000.0 and s["short_put"] == 71800.0
