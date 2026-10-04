"""MCX commodity option contracts for the commodity-options book.

Commodity options are European-style and settle into the underlying FUTURES,
not spot.  Dhan serves them on the MCX_COMM segment and the scrip master
carries them as OPTFUT (option on future), with the matching FUTCOM contract
as the underlying.

There is no index-style /optionchain endpoint for commodities: the chain has
to be assembled from the scrip master (strike grid + security ids) and priced
per strike via /marketfeed/ltp.  See adapters/market_data/commodity_chain.py.

Scrip-master facts (2026-09-26 snapshot, MCX only):

    symbol        options  strike interval   futures
    CRUDEOIL       1100    50                CRUDEOIL
    GOLDM           480    500               GOLDM      (gold mini)
    SILVERM        3298    1000              SILVERM    (silver mini)
    NATURALGAS      516    5                 NATURALGAS

Live LTP probe (2026-10-02) returned: crude 9008, goldmini 147280,
silvermini 227850, natgas 288.1 -- the MCX_COMM data path is confirmed.

Lot sizes are deliberately left None: the scrip master's SEM_LOT_UNITS is a
placeholder ("1.0"), so the real quantity has to come from the margin
calculator, not the master.
"""
from __future__ import annotations

MCX_SEGMENT = "MCX_COMM"
OPTION_INSTRUMENT = "OPTFUT"
FUTURE_INSTRUMENT = "FUTCOM"


class Commodity:
    def __init__(self, symbol: str, futures: str, step: float, lot, label: str):
        self.symbol = symbol
        self.futures = futures
        self.step = float(step)   #: near-the-money strike interval (tiered in reality)
        self.lot = lot            #: resolved from the margin calculator, not the master
        self.label = label

    def __repr__(self):
        return "Commodity(%s, step %.0f, lot %s)" % (self.symbol, self.step, self.lot)


#: MCX lot sizes - the contract multiplier in the commodity's natural unit
#: (barrels / grams / kg / mmBtu).  The scrip master's SEM_LOT_UNITS is a
#: placeholder ("1.0"), so these come from the public MCX contract specs and
#: should be VERIFIED against the margin calculator before any live order is
#: sized off them.
#: The lot value is the P&L MULTIPLIER - contract quantity divided by the quote
#: unit - not the raw contract size.  MCX gold mini is 100g but quoted per 10g,
#: so a 1-point move is Rs10, not Rs100.  GOLDM=10 is confirmed against a live
#: strategy-builder screenshot (Rs33,590 net premium / 1,679.5 credit = 20 units
#: for 2 lots); the others follow the same rule and want the same confirmation.
COMMODITIES = {
    "CRUDEOIL": Commodity("CRUDEOIL", "CRUDEOIL", 50.0, 100, "Crude Oil"),
    "CRUDEOILM": Commodity("CRUDEOILM", "CRUDEOILM", 50.0, 10, "Crude Oil Mini"),
    "GOLDM": Commodity("GOLDM", "GOLDM", 500.0, 10, "Gold Mini"),
    "SILVERM": Commodity("SILVERM", "SILVERM", 1000.0, 5, "Silver Mini"),
    "NATURALGAS": Commodity("NATURALGAS", "NATURALGAS", 5.0, 1250, "Natural Gas"),
    "NATGASMINI": Commodity("NATGASMINI", "NATGASMINI", 5.0, 250, "Natural Gas Mini"),
}


#: The commodity condor book.  Shape 9/2 (short_off 9 strikes, wing 2), Gold Mini
#: 3 lots, and Crude Mini / Nat Gas Mini sized to carry roughly the same rupee
#: risk as Gold (Gold 3 lots ~ Rs18.8k max loss -> Crude 35 lots ~ Rs18.6k,
#: NatGas 8 lots ~ Rs17.9k).  A separate Rs10L paper balance, independent of the
#: index book's own paper account.
BOOK = {
    "short_off": 9,
    "wing": 2,
    "lots": {"GOLDM": 3, "CRUDEOILM": 35, "NATGASMINI": 8},
    "paper_balance": 1_000_000.0,
}


def book_lots(symbol: str) -> int:
    """Lots the commodity book runs for a symbol (default 1 if not sized)."""
    return int(BOOK["lots"].get(str(symbol).upper(), 1))


def spec(symbol: str) -> Commodity:
    key = str(symbol).upper()
    if key not in COMMODITIES:
        raise KeyError("unknown commodity %r (have %s)" % (symbol, sorted(COMMODITIES)))
    return COMMODITIES[key]
