"""PARALLAX — MarketState contract (§6.1 of the design doc).

The single structured snapshot the whole decision chain consumes.  Built by
the perception layer from OHLCV bars; never hand-constructed by a reasoning
or communication component.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .enums import DataQuality, InstrumentType, RegimeClass, VolRegime
from .structure import Bar, Break, LiquidityLevel, Swing, Zone


@dataclass(frozen=True, slots=True)
class InstrumentId:
    symbol: str
    kind: InstrumentType
    exchange: str = "NSE"

    def __str__(self) -> str:
        return f"{self.exchange}:{self.symbol}"


@dataclass
class SessionState:
    name: str = "CLOSED"
    is_open: bool = False
    timezone: str = "Asia/Kolkata"
    session_high: Optional[float] = None
    session_low: Optional[float] = None
    prev_day_high: Optional[float] = None
    prev_day_low: Optional[float] = None
    prev_week_high: Optional[float] = None
    prev_week_low: Optional[float] = None
    session_open: Optional[float] = None
    opening_high: Optional[float] = None   # high of the first N bars of the session
    opening_low: Optional[float] = None    # low of the first N bars of the session


@dataclass
class PriceState:
    last: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    prev_close: Optional[float] = None
    open_today: Optional[float] = None
    high_today: Optional[float] = None
    low_today: Optional[float] = None

    @property
    def spread(self) -> Optional[float]:
        if self.bid is not None and self.ask is not None:
            return self.ask - self.bid
        return None

    @property
    def change_pct(self) -> Optional[float]:
        if self.prev_close:
            return (self.last - self.prev_close) / self.prev_close
        return None


@dataclass
class VolumeState:
    volume: float = 0.0
    relative_volume: Optional[float] = None   # current / rolling average
    avg_volume: Optional[float] = None
    volume_trend: float = 0.0                 # + expanding / - contracting


@dataclass
class VolatilityState:
    atr: Optional[float] = None
    atr_pct: Optional[float] = None
    realized_vol: Optional[float] = None       # annualized decimal
    implied_vol: Optional[float] = None
    vol_percentile: Optional[float] = None     # 0..1 vs lookback
    vol_regime: VolRegime = VolRegime.VOL_UNKNOWN


@dataclass
class StructureState:
    trend: str = "NEUTRAL"                     # BULLISH | BEARISH | NEUTRAL
    bias: str = "NEUTRAL"                      # higher-timeframe bias
    swings: list[Swing] = field(default_factory=list)
    breaks: list[Break] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)
    last_break: Optional[Break] = None
    last_mss: Optional[Break] = None
    swept_opening_low: bool = False     # sell-side sweep of the opening low (bullish)
    swept_opening_high: bool = False    # buy-side sweep of the opening high (bearish)


@dataclass
class LiquidityState:
    levels: list[LiquidityLevel] = field(default_factory=list)
    nearest_buy_side: Optional[float] = None   # resting above price
    nearest_sell_side: Optional[float] = None  # resting below price
    equal_highs: list[float] = field(default_factory=list)
    equal_lows: list[float] = field(default_factory=list)


@dataclass
class IndicatorState:
    values: dict[str, float] = field(default_factory=dict)

    def get(self, name: str, default: float = 0.0) -> float:
        return self.values.get(name, default)

    @property
    def rsi(self) -> float:
        return self.values.get("rsi", 50.0)

    @property
    def adx(self) -> float:
        return self.values.get("adx", 0.0)

    @property
    def atr(self) -> float:
        return self.values.get("atr", 0.0)

    @property
    def ema_fast(self) -> float:
        return self.values.get("ema_fast", 0.0)

    @property
    def ema_slow(self) -> float:
        return self.values.get("ema_slow", 0.0)

    @property
    def vwap(self) -> float:
        return self.values.get("vwap", 0.0)

    @property
    def plus_di(self) -> float:
        return self.values.get("plus_di", 0.0)

    @property
    def minus_di(self) -> float:
        return self.values.get("minus_di", 0.0)


@dataclass
class RegimeState:
    regime: RegimeClass = RegimeClass.UNKNOWN
    trend_direction: str = "NEUTRAL"
    volatility_regime: VolRegime = VolRegime.VOL_UNKNOWN
    session_bias: str = "NEUTRAL"


@dataclass
class MarketEvent:
    type: str
    timestamp: datetime
    description: str
    severity: str = "INFO"                      # INFO | WARN | CRITICAL


@dataclass
class DataQualityState:
    status: DataQuality = DataQuality.GOOD
    issues: list[str] = field(default_factory=list)
    last_bar_ts: Optional[datetime] = None
    staleness_seconds: Optional[float] = None
    missing_bars: int = 0
    duplicate_bars: int = 0
    timestamp_discontinuities: int = 0

    @property
    def healthy(self) -> bool:
        return self.status == DataQuality.GOOD


@dataclass
class TimeframeState:
    timeframe: str                               # "1m", "5m", "15m", "1h", "1d"
    bars: list[Bar] = field(default_factory=list)
    last: Optional[Bar] = None
    high: Optional[float] = None
    low: Optional[float] = None


@dataclass
class MarketState:
    instrument: InstrumentId
    timestamp: datetime
    session: SessionState = field(default_factory=SessionState)
    timeframes: dict[str, TimeframeState] = field(default_factory=dict)
    price: Optional[PriceState] = None
    volume: VolumeState = field(default_factory=VolumeState)
    volatility: VolatilityState = field(default_factory=VolatilityState)
    structure: StructureState = field(default_factory=StructureState)
    liquidity: LiquidityState = field(default_factory=LiquidityState)
    indicators: IndicatorState = field(default_factory=IndicatorState)
    regime: RegimeState = field(default_factory=RegimeState)
    events: list[MarketEvent] = field(default_factory=list)
    data_quality: DataQualityState = field(default_factory=DataQualityState)

    def timeframe(self, name: str) -> Optional[TimeframeState]:
        return self.timeframes.get(name)

    @property
    def last_price(self) -> Optional[float]:
        return self.price.last if self.price else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": str(self.instrument),
            "timestamp": self.timestamp.isoformat(),
            "price": self.last_price,
            "regime": self.regime.regime.value,
            "trend": self.structure.trend,
            "bias": self.structure.bias,
            "data_quality": self.data_quality.status.value,
        }
