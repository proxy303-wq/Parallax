"""Vol timing: the fixed-strike reconstruction.

This is where the study went wrong first, and the error is invisible in the
output - it produced a t-statistic of -21 that looked like a decisive finding
and was entirely an artefact.  The tell was internal inconsistency: the study
reported that a 0DTE move beat the entry straddle 24% of the time, while the
same straddles supposedly won 1.2% of the time.  Both cannot be true.
"""
import datetime

import pytest

from parallax.apps.research.vol_timing import IST, legs_at, measure, next_expiry

STEP = 50.0


def mk(spot, call, put):
    return {"spot": spot,
            "CALL": {float(k): (float(v), float(v), float(v))
                     for k, v in call.items()},
            "PUT": {float(k): (float(v), float(v), float(v))
                    for k, v in put.items()}}


#: entry atm 23000, exit atm 23100.  The 23000 strike now sits two steps
#: BELOW the atm, so it must be read at -100, not at 0.
MOVED = mk(23100.0, {-100.0: 120.0, 0.0: 90.0}, {-100.0: 20.0, 0.0: 130.0})


def test_a_fixed_strike_is_read_at_its_drifted_offset():
    v = legs_at(MOVED, [(23000.0, "CALL"), (23000.0, "PUT")], STEP)
    assert v == pytest.approx(140.0)          # 120 call + 20 put, at offset -100


def test_it_is_not_the_new_atm_straddle():
    """The regression: re-anchoring to the new atm re-strikes the position."""
    fixed = legs_at(MOVED, [(23000.0, "CALL"), (23000.0, "PUT")], STEP)
    reanchored = legs_at(MOVED, [(23100.0, "CALL"), (23100.0, "PUT")], STEP)
    assert reanchored == pytest.approx(220.0)
    assert fixed != reanchored


def test_an_unreachable_strike_is_none_not_a_silent_zero():
    assert legs_at(MOVED, [(23000.0, "CALL"), (29999.0, "PUT")], STEP) is None


def test_legs_at_its_own_atm_is_the_plain_straddle():
    r = mk(23000.0, {0.0: 60.0}, {0.0: 40.0})
    assert legs_at(r, [(23000.0, "CALL"), (23000.0, "PUT")], STEP) == \
        pytest.approx(100.0)


def test_next_expiry_finds_the_coming_tuesday():
    assert next_expiry("NIFTY", datetime.date(2026, 9, 25)).weekday() == 1
    assert next_expiry("NIFTY", datetime.date(2026, 9, 29)) == \
        datetime.date(2026, 9, 29)            # expiry day is itself, dte 0


def _session(day, n, spot, prices):
    """n five-minute bars on one day, all carrying the same ladder."""
    out = {}
    base = datetime.datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST)
    for k in range(n):
        out[int((base + datetime.timedelta(minutes=5 * k)).timestamp())] = \
            mk(spot, prices, prices)
    return out


def test_a_straddle_that_should_win_is_measured_as_a_win():
    """End to end on the shape that caught the bug: index runs 200 points.

    The entry straddle costs 100.  Held to the close the 23000 strike is worth
    200, so the trade must show a profit - the re-anchoring version showed a
    loss on exactly this bar.
    """
    day = datetime.date(2026, 9, 22)          # a Tuesday week
    lad = {}
    lad.update(_session(day, 25, 23000.0, {0.0: 50.0}))
    # last bar of the same session: index 200 higher
    last = max(lad)
    lad[last] = mk(23200.0, {-200.0: 200.0, 0.0: 90.0}, {-200.0: 0.5, 0.0: 130.0})
    rows = measure(lad, "NIFTY", progress=lambda m: None)
    assert len(rows) == 1
    assert rows[0]["move"] == pytest.approx(200.0)
    assert rows[0]["strad"] > 0, "a 200-point run must not show as a loss"
