"""Day routing for the option-selling engine.

The expiry days are fixed by the exchanges and were read off Dhan's expirylist:

    NIFTY      weekly    Tuesday
    BANKNIFTY  monthly   last Tuesday
    FINNIFTY   monthly   last Tuesday
    SENSEX     weekly    Thursday
    BANKEX     monthly   last Thursday

The last Tuesday stacks NIFTY, BANKNIFTY and FINNIFTY on one expiry - about
Rs 10.8-14.7 lakh of margin at 5 lots against an Rs 8L account - so the rule is
BANKNIFTY alone that day, with FINNIFTY dropped entirely (same expiry, same bet).
The last Thursday stacks SENSEX and BANKEX for about Rs 7.0 lakh, which fits.
"""
import datetime

from parallax.config.schedule import (
    futures_active, is_0dte_day, options_active, options_plan,
)

D = datetime.date


def test_plain_tuesday_is_nifty():
    assert options_plan(D(2026, 9, 1)) == ["NIFTY"]
    assert options_plan(D(2026, 9, 8)) == ["NIFTY"]
    assert options_plan(D(2026, 9, 22)) == ["NIFTY"]


def test_last_tuesday_is_banknifty_only():
    # 2026-09-29 and 2026-10-27 are the last Tuesdays of their months
    assert options_plan(D(2026, 9, 29)) == ["BANKNIFTY"]
    assert options_plan(D(2026, 10, 27)) == ["BANKNIFTY"]
    assert "NIFTY" not in options_plan(D(2026, 9, 29))
    assert "FINNIFTY" not in options_plan(D(2026, 9, 29))


def test_plain_thursday_is_sensex():
    assert options_plan(D(2026, 9, 3)) == ["SENSEX"]
    assert options_plan(D(2026, 10, 1)) == ["SENSEX"]


def test_last_thursday_adds_bankex():
    assert options_plan(D(2026, 9, 24)) == ["SENSEX", "BANKEX"]
    assert options_plan(D(2026, 10, 29)) == ["SENSEX", "BANKEX"]


def test_other_days_are_futures():
    for d in (D(2026, 9, 2), D(2026, 9, 4), D(2026, 9, 7)):   # Wed Fri Mon
        assert options_plan(d) == []
        assert futures_active(d) is True
        assert options_active(d) is False


def test_expiry_days_suppress_futures():
    for d in (D(2026, 9, 1), D(2026, 9, 29), D(2026, 9, 3), D(2026, 9, 24)):
        assert is_0dte_day(d) is True
        assert futures_active(d) is False
        assert options_active(d) is True


def test_monthly_indices_fire_exactly_once_a_month():
    """NIFTY and SENSEX are weekly; BANKNIFTY, FINNIFTY and BANKEX monthly."""
    counts = {}
    d = D(2026, 9, 1)
    while d.month == 9:
        for sym in options_plan(d):
            counts[sym] = counts.get(sym, 0) + 1
        d += datetime.timedelta(days=1)
    assert counts.get("NIFTY") == 4          # Sep 1, 8, 15, 22
    assert counts.get("SENSEX") == 4         # Sep 3, 10, 17, 24
    assert counts.get("BANKNIFTY") == 1      # Sep 29
    assert counts.get("BANKEX") == 1         # Sep 24
    assert "FINNIFTY" not in counts          # dropped: same day and bet as BANKNIFTY


def test_nifty_and_banknifty_never_share_a_day():
    """The whole point of the last-Tuesday rule."""
    d = D(2026, 9, 1)
    while d <= D(2026, 12, 31):
        p = options_plan(d)
        assert not ("NIFTY" in p and "BANKNIFTY" in p), d
        d += datetime.timedelta(days=1)
