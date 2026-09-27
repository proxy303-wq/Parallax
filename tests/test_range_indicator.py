"""The range indicator's calibration, checked against the numbers it came from.

The point of these tests is that the table must PREDICT the results the book
actually produced.  If the table drifts away from those two known cases, the
indicator is no longer describing the market it was measured on.
"""
import pytest

from parallax.adapters.market_data import range_indicator as ri


def test_the_table_reproduces_the_nifty_book():
    """NIFTY 4/-3: short 200 + credit ~38 = 238 break-even on a ~172 straddle
    = 1.38x, between p75 and p90.  Measured win rate 0.87."""
    p = ri.prob_profit("NIFTY", 200.0, 38.0, 172.32)
    assert 0.85 <= p <= 0.92, p


def test_the_table_reproduces_the_sensex_book():
    """SENSEX 5/-3: short 500 + credit ~100 = 600 on ~622 = 0.97x.  Measured
    win rate 0.74."""
    p = ri.prob_profit("SENSEX", 500.0, 100.0, 621.52)
    assert 0.70 <= p <= 0.82, p


def test_the_credit_is_what_makes_the_difference():
    """Ignoring the credit under-predicts the win rate, which is the bug this
    test exists to prevent."""
    without = ri.prob_move_within("SENSEX", 500.0, 621.52)
    with_credit = ri.prob_profit("SENSEX", 500.0, 100.0, 621.52)
    assert with_credit > without + 0.05, (without, with_credit)


def test_probability_rises_with_distance():
    last = 0.0
    for pts in (50, 100, 150, 200, 300, 400):
        p = ri.prob_move_within("NIFTY", pts, 172.32)
        assert p > last, (pts, p, last)
        last = p


def test_zero_distance_is_never_certain():
    assert ri.prob_move_within("NIFTY", 0.0, 172.32) <= 0.10


def test_strikes_for_profit_is_sane():
    n90 = ri.strikes_for_profit("NIFTY", 172.32, 38.0, 0.90)
    n95 = ri.strikes_for_profit("NIFTY", 172.32, 38.0, 0.95)
    assert n90 and n95 and n95 >= n90 >= 1
    from parallax.config.indices import spec
    step = spec("NIFTY").step
    assert ri.prob_profit("NIFTY", n90 * step, 38.0, 172.32) >= 0.90


def test_an_uncalibrated_index_says_so_rather_than_guessing():
    assert ri.calibrate("BANKNIFTY") == (None, None)
    assert ri.prob_profit("BANKNIFTY", 500.0, 100.0, 550.0) is None
    assert ri.strikes_for_profit("BANKEX", 6000.0, 500.0, 0.90) is None


def test_the_range_always_exceeds_the_net_move():
    """This is the whole thesis: the market roams further than it finishes.
    Every percentile of the range table must dominate the same percentile of
    the net-move table."""
    for sym in ("NIFTY", "SENSEX"):
        move, rng = ri.calibrate(sym)
        for p in ri.PS:
            assert rng[p] > move[p], (sym, p)
