"""The stitched fixed-expiry panel.

Two traps live here, and both of them silently produce a plausible-looking
backtest rather than an error:

  * a rolling expiry code does NOT follow one contract - it ages, so reading a
    single code across a weekly rollover swaps the instrument under the position
  * a fixed strike must be read at its DRIFTED offset, never at its entry offset,
    or the position is re-struck to the new ATM every bar

The second one already cost one study a t-statistic of -21 that meant nothing.
"""
import datetime

import pytest

from parallax.apps.research.buy_panel import (IST, _target_expiry, atm_of,
                                              code_for, expiry_days, leg_value,
                                              stitch)

D = datetime.date


def ts(y, m, d, hh=9, mm=15):
    return int(datetime.datetime(y, m, d, hh, mm, tzinfo=IST).timestamp())


def row(spot, call=None, put=None):
    return {"spot": float(spot),
            "CALL": {float(k): (float(v), float(v), float(v))
                     for k, v in (call or {}).items()},
            "PUT": {float(k): (float(v), float(v), float(v))
                    for k, v in (put or {}).items()}}


def test_code_for_counts_from_the_nearest_live_expiry():
    exps = [D(2026, 1, 6), D(2026, 1, 13), D(2026, 1, 20), D(2026, 1, 27)]
    assert code_for(exps, D(2026, 1, 7), D(2026, 1, 20)) == 2
    assert code_for(exps, D(2026, 1, 14), D(2026, 1, 20)) == 1
    assert code_for(exps, D(2026, 1, 20), D(2026, 1, 20)) == 1   # on the day
    assert code_for(exps, D(2026, 1, 7), D(2025, 1, 1)) is None


def test_an_expiry_ages_from_the_far_code_to_the_near_one():
    """The whole point: one contract, many codes, over its life."""
    exps = [D(2026, 1, 6), D(2026, 1, 13), D(2026, 1, 20)]
    target = D(2026, 1, 20)
    assert code_for(exps, D(2026, 1, 5), target) == 3
    assert code_for(exps, D(2026, 1, 7), target) == 2
    assert code_for(exps, D(2026, 1, 14), target) == 1


def test_stitch_reads_each_day_from_the_code_that_owns_it():
    exps = [D(2026, 1, 6), D(2026, 1, 13)]
    c2 = {ts(2026, 1, 5): row(100.0)}          # the 13th is 2nd nearest then
    c1 = {ts(2026, 1, 7): row(200.0)}          # by the 7th it is nearest
    p = stitch({1: c1, 2: c2}, exps, D(2026, 1, 13))
    assert len(p) == 2
    assert p[ts(2026, 1, 5)]["spot"] == 100.0
    assert p[ts(2026, 1, 7)]["spot"] == 200.0


def test_stitch_drops_bars_after_the_expiry():
    exps = [D(2026, 1, 6), D(2026, 1, 13)]
    c1 = {ts(2026, 1, 7): row(200.0), ts(2026, 1, 14): row(999.0)}
    p = stitch({1: c1}, exps, D(2026, 1, 13))
    assert ts(2026, 1, 14) not in p


def test_stitch_is_empty_when_no_code_covers_the_expiry():
    exps = [D(2026, 1, 6), D(2026, 1, 13)]
    assert stitch({1: {ts(2026, 1, 7): row(1.0)}}, exps, D(2026, 2, 24)) == {}


def test_a_fixed_strike_is_read_at_its_drifted_offset():
    """Entry atm 23000, now atm 23100: the 23000 strike sits at -100."""
    r = row(23100.0, {-100.0: 120.0, 0.0: 90.0}, {-100.0: 20.0, 0.0: 130.0})
    assert leg_value(r, [(23000.0, "CALL", 1.0), (23000.0, "PUT", 1.0)],
                     50.0) == pytest.approx(140.0)
    assert leg_value(r, [(23100.0, "CALL", 1.0), (23100.0, "PUT", 1.0)],
                     50.0) == pytest.approx(220.0)


def test_a_spread_is_long_minus_short():
    r = row(23000.0, {0.0: 180.0, 250.0: 80.0})
    legs = [(23000.0, "CALL", 1.0), (23250.0, "CALL", -1.0)]
    assert leg_value(r, legs, 50.0) == pytest.approx(100.0)


def test_an_unreachable_strike_is_none_not_zero():
    r = row(23000.0, {0.0: 180.0})
    assert leg_value(r, [(23000.0, "CALL", 1.0), (29999.0, "CALL", -1.0)],
                     50.0) is None


def test_atm_snaps_to_the_strike_grid():
    assert atm_of({"spot": 23123.0}, 50.0) == 23100.0
    assert atm_of({"spot": 23128.0}, 50.0) == 23150.0


def test_expiry_days_keeps_only_tuesdays():
    days = [D(2026, 1, 5), D(2026, 1, 6), D(2026, 1, 13)]   # Mon, Tue, Tue
    assert expiry_days(days) == [D(2026, 1, 6), D(2026, 1, 13)]


def test_target_expiry_honours_the_dte_band():
    exps = [D(2026, 1, 6), D(2026, 1, 13), D(2026, 1, 20), D(2026, 1, 27)]
    day = D(2026, 1, 5)
    # 1, 8, 15, 22 days out
    assert _target_expiry(exps, day, 20, 45) == D(2026, 1, 27)
    assert _target_expiry(exps, day, 14, 18) == D(2026, 1, 20)
    assert _target_expiry(exps, day, 30, 45) is None
