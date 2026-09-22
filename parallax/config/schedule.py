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

The last Thursday puts SENSEX and BANKEX together for about Rs 7.0 lakh, which
fits, so both run.

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
    """Which index condors to run on this date, in order.

    Tuesday  : BANKNIFTY on the last Tuesday of the month, else NIFTY.
    Thursday : SENSEX always, plus BANKEX on the last Thursday.
    """
    d = _d(d)
    if d.weekday() == NIFTY_EXPIRY_WEEKDAY:
        return ["BANKNIFTY"] if _is_last_of_month(d, NIFTY_EXPIRY_WEEKDAY) else ["NIFTY"]
    if d.weekday() == SENSEX_EXPIRY_WEEKDAY:
        out = ["SENSEX"]
        if _is_last_of_month(d, SENSEX_EXPIRY_WEEKDAY):
            out.append("BANKEX")
        return out
    return []


def active_strategies(d) -> dict:
    return {"futures": futures_active(d), "options": options_plan(d)}
