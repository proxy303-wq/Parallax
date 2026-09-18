"""Per-instrument contract specifications (point value, lot, currency, costs, sizing).

Point values and lot sizes are read from the broker's instrument master (see
smcagent/adapters/instruments.py provenance): NIFTY lot 65, BANKNIFTY 30,
FINNIFTY 60.  Cost models differ by venue:

* NSE index futures are STT/brokerage/exchange dominated — ~1-2 bps per side
  of notional plus a tick of slippage.
* Crypto perpetuals are taker-fee dominated — 5 bps per side (Delta taker)
  plus 5 bps slippage.

Sizing model: position_size is in *units* (lots for futures, coins for crypto)
with a per-venue minimum step; point_value is P&L per 1 point move per unit.

* Index futures: 1 unit = 1 lot, point_value = lot size (65/30/60 rupees).
* Crypto: 1 unit = 1 coin, point_value = 1 (delta-1), min_step = 0.001 coin.

stop_mult is the default ATR multiple for the initial stop — wider for crypto,
whose 20 bps round-trip cost demands a stop wide enough that friction stays a
small fraction of risk (the corpus's "cost-to-vol" defect).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    point_value: float        # P&L per 1 point move, per 1 unit
    lot_size: float           # units per lot (futures) / informational (crypto)
    currency: str
    flatten_at_session_end: bool
    default_capital: float
    tick_size: float = 0.05
    fee_rate: float = 0.0001       # per-side, fraction of notional
    slippage: float = 0.0001       # per-side, fraction of price
    min_step: float = 1.0          # minimum tradable quantity increment
    stop_mult: float = 2.0         # default ATR multiple for the initial stop


SPECS: dict[str, InstrumentSpec] = {
    "NIFTY": InstrumentSpec(65.0, 65.0, "INR", True, 500_000.0, 0.05, 0.0001, 0.000002, 1.0, 2.0),
    "BANKNIFTY": InstrumentSpec(30.0, 30.0, "INR", True, 500_000.0, 0.10, 0.0001, 0.000002, 1.0, 2.0),
    "FINNIFTY": InstrumentSpec(60.0, 60.0, "INR", True, 500_000.0, 0.05, 0.0001, 0.000002, 1.0, 2.0),
    "BTC": InstrumentSpec(1.0, 1.0, "USD", False, 10_000.0, 0.5, 0.0005, 0.0005, 0.001, 4.0),
    "ETH": InstrumentSpec(1.0, 1.0, "USD", False, 10_000.0, 0.1, 0.0005, 0.0005, 0.001, 4.0),
    "SOL": InstrumentSpec(1.0, 1.0, "USD", False, 10_000.0, 0.01, 0.0005, 0.0005, 0.01, 4.0),
    "XRP": InstrumentSpec(1.0, 1.0, "USD", False, 10_000.0, 0.001, 0.0005, 0.0005, 1.0, 4.0),
    "XAUT": InstrumentSpec(1.0, 1.0, "USD", False, 10_000.0, 0.01, 0.0005, 0.0005, 0.01, 4.0),
}


def spec_for(symbol: str) -> InstrumentSpec:
    s = SPECS.get(str(symbol).upper())
    if s is None:
        raise KeyError(f"unknown instrument {symbol!r}; known: {', '.join(sorted(SPECS))}")
    return s
