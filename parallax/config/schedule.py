"""Trading session scheduler - which strategy runs on which day.

Expiry days are fixed by the exchanges:

    NIFTY      weekly    Tuesday
    BANKNIFTY  monthly   last Tuesday
    FINNIFTY   monthly   last Tuesday
    SENSEX     weekly    Thursday
    BANKEX     monthly   last Thursday

The last Tuesday puts NIFTY, BANKNIFTY and FINNIFTY on the same expiry. At 5
lots that is Rs 10.8-14.7 lakh of margin against an Rs 8 lakh book, so the
rule is: on the last Tuesday trade BANKNIFTY ALONE and skip NIFTY. FINNIFTY is
dropped entirely - it expires the same day as BANKNIFTY and is the same bet.

The last Thursday puts SENSEX and BANKEX together.  They used to run as a
pair (about Rs 7.0 lakh, which fits the book), but two condors in one session
is two short-vol positions on the same market: the indices are ~90% correlated
and the max losses land together.  So the monthly displaces the weekly here
too - on the last Thursday it is BANKEX ALONE and SENSEX is skipped.

Every other weekday the futures engine runs and the options engine is idle.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

NIFTY_EXPIRY_WEEKDAY = 1       # Tuesday
SENSEX_EXPIRY_WEEKDAY = 3      # Thursday


def _d(d) -> date:
    return d.date() if isinstance(d, datetime) else d


def _is_last_of_month(d: date, weekday: int) -> bool:
    return d.weekday() == weekday and (d + timedelta(days=7)).month != d.month


def is_0dte_day(d) -> bool:
    """Any day an index option expires."""
    d = _d(d)
    return d.weekday() in (NIFTY_EXPIRY_WEEKDAY, SENSEX_EXPIRY_WEEKDAY)


def futures_active(d) -> bool:
    """Futures engine fires on every day EXCEPT the option expiry days."""
    return not is_0dte_day(d)


def options_active(d) -> bool:
    """True when at least one index option expires and should be traded."""
    return bool(options_plan(d))


def options_plan(d) -> list:
    """Which index condor runs on this date.

    EXACTLY ONE per day, always:

        normal Tuesday   NIFTY
        last Tuesday     BANKNIFTY   (the monthly takes the slot)
        normal Thursday  SENSEX
        last Thursday    BANKEX      (the monthly takes the slot)

    The monthlies displace the weeklies rather than joining them.  Two condors
    on one day is two short-vol positions on the same market expiring together
    - at 8 lots that was Rs 12.6 lakh against an Rs 8 lakh book, and the
    correlation means the max losses land on the same session.
    """
    d = _d(d)
    if d.weekday() == NIFTY_EXPIRY_WEEKDAY:
        return ["BANKNIFTY"] if _is_last_of_month(d, NIFTY_EXPIRY_WEEKDAY) else ["NIFTY"]
    if d.weekday() == SENSEX_EXPIRY_WEEKDAY:
        return ["BANKEX"] if _is_last_of_month(d, SENSEX_EXPIRY_WEEKDAY) else ["SENSEX"]
    return []


def active_strategies(d) -> dict:
    return {"futures": futures_active(d), "options": options_plan(d)}
