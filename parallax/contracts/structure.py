"""PARALLAX — structure & liquidity value types.

Mirrors the market-structure vocabulary of the design doc (FVG/OB/BOS/MSS/
CHoCH, liquidity levels).  Every type carrying a bar index also carries the
earliest index at which a live system could have *known* about it, so no
look-ahead can sneak through the perception layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class SwingKind(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class BreakKind(str, Enum):
    BOS = "BOS"        # break of structure — trend continuation
    CHOCH = "CHoCH"    # change of character — reversal
    MSS = "MSS"        # market structure shift (CHOCH synonym used in SMC)


class ZoneKind(str, Enum):
    OB = "OB"          # order block: last opposing candle before displacement
    FVG = "FVG"        # fair value gap: three-candle imbalance
    IFVG = "IFVG"      # inverted FVG: an FVG traded through, polarity flipped


class LiquiditySide(str, Enum):
    BUY = "BUY"        # buy-side liquidity (rests above price)
    SELL = "SELL"      # sell-side liquidity (rests below price)


@dataclass(frozen=True, slots=True)
class Bar:
    """A closed OHLCV bar.  'ts' is the bar OPEN time, timezone-aware."""
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def bullish(self) -> bool:
        return self.close >= self.open

    def as_dict(self) -> dict[str, Any]:
        return {"ts": self.ts.isoformat(), "o": self.open, "h": self.high,
                "l": self.low, "c": self.close, "v": self.volume}


@dataclass(frozen=True, slots=True)
class Swing:
    """A confirmed fractal pivot.  'known_idx' is the earliest index at which
    it could be confirmed (idx + right strength); a live system must never act
    on a swing before 'known_idx'."""
    kind: SwingKind
    price: float
    idx: int
    known_idx: int
    ts: datetime
    known_ts: datetime
    internal: bool = False


@dataclass(frozen=True, slots=True)
class Break:
    kind: BreakKind
    direction: str          # "bullish" | "bearish"
    price: float
    idx: int
    known_idx: int
    ts: datetime
    known_ts: datetime
    level: float            # the swing level that was broken


@dataclass(frozen=True, slots=True)
class Zone:
    kind: ZoneKind
    direction: str          # "bullish" | "bearish"
    top: float
    bottom: float
    idx: int
    known_idx: int
    ts: datetime
    known_ts: datetime
    mitigated: bool = False


@dataclass(frozen=True, slots=True)
class LiquidityLevel:
    side: LiquiditySide
    price: float
    idx: int
    known_idx: int
    ts: datetime
    known_ts: datetime
    swept: bool = False
    count: int = 1          # number of touches forming equal highs/lows
