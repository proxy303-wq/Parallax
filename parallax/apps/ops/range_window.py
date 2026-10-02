"""Range-anchored condor window.

The chosen rule: predict the day's range from recent history, then place the
short strikes some fixed number of buffer strikes beyond that range on each
side.

    R      = EWMA(lambda) of the trailing realised excursions
    half   = R + buffer * step
    shorts = ATM +/- half, snapped to the nearest strike
    wings  = wing strikes beyond the shorts
"""
from __future__ import annotations

LAMBDA = 0.25
WING = 3          #: strikes beyond the short strike


def ewma(values: list[float], lam: float = LAMBDA) -> float:
    """The trailing average, with recent days weighted more heavily."""
    e = None
    for x in values:
        e = x if e is None else (1.0 - lam) * e + lam * x
    return e or 0.0


def predicted_range(excursions: list[float], lam: float = LAMBDA) -> float:
    """One number: how far the index is likely to stray off the entry print."""
    return ewma(excursions, lam)


def window_half(pred: float, buffer_strikes: int, step: float) -> float:
    """Half-width of the condor = predicted range plus a strike buffer."""
    return pred + buffer_strikes * step


def short_off_strikes(half: float, step: float) -> int:
    """Snap the half-width to a whole number of strikes."""
    return int(round(half / step))


def strikes(excursions: list[float], atm: float, step: float,
            buffer_strikes: int = 2, wing: int = WING) -> dict:
    """Full condor strike map from a history of realised excursions."""
    pred = predicted_range(excursions)
    half = window_half(pred, buffer_strikes, step)
    off = short_off_strikes(half, step) * step
    return {
        "predicted_range": round(pred, 1),
        "window_half": round(off, 1),
        "short_call": atm + off,
        "short_put": atm - off,
        "long_call": atm + off + wing * step,
        "long_put": atm - off - wing * step,
        "short_off": int(off / step),
    }
