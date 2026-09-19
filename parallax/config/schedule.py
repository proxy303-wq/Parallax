"""Trading session scheduler — which strategy is active on which day.

NIFTY weekly options expiry is Tuesday.  On the expiry day the 0DTE options
engine is active and the futures engine is suppressed (avoids directional and
margin clashes between the two); every other trading day the futures engine
runs and the 0DTE options engine is idle.
"""
from __future__ import annotations

from datetime import date, datetime

NIFTY_EXPIRY_WEEKDAY = 1   # Tuesday


def is_0dte_day(d: date | datetime) -> bool:
    d = d.date() if isinstance(d, datetime) else d
    return d.weekday() == NIFTY_EXPIRY_WEEKDAY


def futures_active(d: date | datetime) -> bool:
    """Futures engine fires on every day EXCEPT the 0DTE expiry day."""
    return not is_0dte_day(d)


def options_active(d: date | datetime) -> bool:
    """0DTE options engine fires only on the weekly expiry day."""
    return is_0dte_day(d)


def active_strategies(d: date | datetime) -> dict[str, bool]:
    return {"futures": futures_active(d), "options_0dte": options_active(d)}
