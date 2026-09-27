"""The range indicator's calibration, checked against the numbers it came from.

The point is that the tables must PREDICT the results the book actually
produced.  If they drift away from those cases, the indicator has stopped
describing the market it was measured on.
"""
import datetime

import pytest

from parallax.adapters.market_data import range_indicator as ri


# ---- it reproduces the book -------------------------------------------------
def test_it_reproduces_the_nifty_book():
    """NIFTY 4/-3 on expiry day: short 200 + credit ~38 = 238 break-even on a
    ~172 straddle = 1.38x.  Measured win rate 0.87."""
    p = ri.prob_profit("NIFTY", 200.0, 38.0, 172.32, 0)
    assert 0.85 <= p <= 0.92, p


def test_it_reproduces_the_sensex_book():
    """SENSEX 5/-3 on expiry day: 600 break-even on ~622 = 0.97x.  Measured
    win rate 0.74."""
    p = ri.prob_profit("SENSEX", 500.0, 100.0, 621.52, 0)
    assert 0.70 <= p <= 0.82, p


def test_the_credit_is_what_makes_the_difference():
    """Using the short distance instead of the break-even under-predicts the
    win rate -- the bug this pair exists to catch."""
    without = ri.prob_move_within("SENSEX", 500.0, 621.52, 0)
    with_credit = ri.prob_profit("SENSEX", 500.0, 100.0, 621.52, 0)
    assert with_credit > without + 0.05, (without, with_credit)


# ---- the horizon is the whole point ----------------------------------------
def test_the_multiplier_decays_as_expiry_approaches():
    """A session is a smaller share of a longer straddle, so the multiple of
    the straddle that one day represents must FALL as days-to-expiry rises."""
    meds = []
    for dte in (0, 1, 3, 6):
        mx, _, _, _ = ri.calibrate("NIFTY", dte)
        meds.append(mx[50])
    assert meds == sorted(meds, reverse=True), meds
    assert meds[0] > meds[-1] * 1.5, meds


def test_using_the_wrong_horizon_is_the_bug_we_fixed():
    """An expiry-day multiplier read against a ten-day straddle understates the
    move about twofold.  Both readings are 'correct' for their own horizon."""
    day0, _, _, _ = ri.calibrate("NIFTY", 0)
    day10, _, _, _ = ri.calibrate("NIFTY", 10)
    assert day0[50] > day10[50] * 1.5, (day0[50], day10[50])


def test_buckets_are_what_they_say():
    assert ri.bucket_for(0) == "0"
    assert ri.bucket_for(1) == "1"
    assert ri.bucket_for(2) == "2"
    assert ri.bucket_for(4) == "3-4"
    assert ri.bucket_for(9) == "5+"


def test_a_thin_bucket_blends_rather_than_guessing():
    """NIFTY at 2 days out has one observation.  It must fall back to its
    neighbours and SAY it did, not present a single sample as a calibration."""
    mx, rg, buck, n = ri.calibrate("NIFTY", 2)
    assert buck.endswith("*"), buck
    assert mx and rg
    one, _, _, _ = ri.calibrate("NIFTY", 1)
    three, _, _, _ = ri.calibrate("NIFTY", 3)
    # the multiplier FALLS with days-to-expiry, so the blend sits between them
    assert three[50] <= mx[50] <= one[50], (three[50], mx[50], one[50])


# ---- monotonicity and sanity -----------------------------------------------
def test_probability_rises_with_distance():
    last = 0.0
    for pts in (50, 100, 150, 200, 300, 400):
        p = ri.prob_move_within("NIFTY", pts, 172.32, 0)
        assert p > last, (pts, p, last)
        last = p


def test_zero_distance_is_never_certain():
    assert ri.prob_move_within("NIFTY", 0.0, 172.32, 0) <= 0.10


def test_strikes_for_profit_is_sane():
    from parallax.config.indices import spec
    n90 = ri.strikes_for_profit("NIFTY", 172.32, 38.0, 0.90, 0)
    n95 = ri.strikes_for_profit("NIFTY", 172.32, 38.0, 0.95, 0)
    assert n90 and n95 and n95 >= n90 >= 1
    assert ri.prob_profit("NIFTY", n90 * spec("NIFTY").step, 38.0, 172.32, 0) >= 0.90


def test_an_uncalibrated_index_says_so_rather_than_guessing():
    assert ri.calibrate("BANKNIFTY", 0)[0] is None
    assert ri.prob_profit("BANKNIFTY", 500.0, 100.0, 550.0, 0) is None
    assert ri.strikes_for_profit("BANKEX", 6000.0, 500.0, 0.90, 0) is None


def test_the_range_always_exceeds_the_net_move():
    """The thesis: the market roams further than it finishes.  Every percentile
    of every range table must dominate the same percentile of the move table."""
    for sym, table in ri.CAL.items():
        for buck, d in table.items():
            for p in ri.PS:
                assert d["range"][p] > d["move"][p], (sym, buck, p)


def test_days_to_expiry_reads_the_contract():
    assert ri.days_to_expiry("2026-10-06", datetime.date(2026, 10, 6)) == 0
    assert ri.days_to_expiry("2026-10-06", datetime.date(2026, 10, 5)) == 1
    assert ri.days_to_expiry("2026-10-06", datetime.date(2026, 9, 29)) == 7
