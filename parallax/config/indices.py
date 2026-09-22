"""Per-index contract facts for the option-selling engine.

Verified against Dhan, not assumed:

  * underlying scrip ids - NIFTY 13 and BANKNIFTY 25 and FINNIFTY 27 all
    returned their known spot levels from /optionchain; SENSEX 51 returned
    74,529 and BANKEX 69 returned 63,529, which match the BSE levels.
  * lot sizes - read from the Dhan scrip master (SEM_LOT_UNITS on OPTIDX):
    NIFTY 65, BANKNIFTY 30, FINNIFTY 60, SENSEX 20, BANKEX 30.
  * strike steps - confirmed from the listed strikes (SENSEX 84,600,
    BANKEX 67,500, BANKNIFTY 72,600 are all multiples of 100; FINNIFTY
    30,050 is a multiple of 50).

Exchange matters for every order and for the live feed: the BSE indices are
BSE_FNO, everything else NSE_FNO.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IndexSpec:
    symbol: str
    underlying_id: int
    exchange_segment: str
    lot: int
    step: int
    currency: str = "INR"


INDICES: dict[str, IndexSpec] = {
    "NIFTY":     IndexSpec("NIFTY", 13, "NSE_FNO", 65, 50),
    "BANKNIFTY": IndexSpec("BANKNIFTY", 25, "NSE_FNO", 30, 100),
    "FINNIFTY":  IndexSpec("FINNIFTY", 27, "NSE_FNO", 60, 50),
    "SENSEX":    IndexSpec("SENSEX", 51, "BSE_FNO", 20, 100),
    "BANKEX":    IndexSpec("BANKEX", 69, "BSE_FNO", 30, 100),
}


def spec(symbol: str) -> IndexSpec:
    s = str(symbol).upper()
    if s not in INDICES:
        raise KeyError("unknown index %r (have %s)" % (symbol, sorted(INDICES)))
    return INDICES[s]
