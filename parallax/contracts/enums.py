"""PARALLAX — canonical enums (the shared vocabulary of the system).

Every subsystem speaks these exact strings so module boundaries are type-safe
and machine-readable.  Values are stable: they form part of the on-disk audit
trail and must never be silently renamed.
"""
from __future__ import annotations

from enum import Enum


class InstrumentType(str, Enum):
    INDEX_SPOT = "INDEX_SPOT"
    INDEX_FUTURE = "INDEX_FUTURE"
    INDEX_OPTION = "INDEX_OPTION"
    CRYPTO_PERP = "CRYPTO_PERP"
    CRYPTO_SPOT = "CRYPTO_SPOT"


class Market(str, Enum):
    NIFTY = "NIFTY"
    BANKNIFTY = "BANKNIFTY"
    FINNIFTY = "FINNIFTY"
    SENSEX = "SENSEX"
    BTC = "BTC"
    ETH = "ETH"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    WAIT = "WAIT"
    EXIT = "EXIT"
    ABORT = "ABORT"


class DecisionClass(str, Enum):
    WAIT = "WAIT"
    TRADE = "TRADE"
    HOLD = "HOLD"
    EXIT = "EXIT"
    ABORT = "ABORT"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class TimeInForce(str, Enum):
    DAY = "DAY"
    IOC = "IOC"
    GTC = "GTC"
    FOK = "FOK"


class OrderStatus(str, Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class SystemMode(str, Enum):
    OFFLINE = "OFFLINE"
    OBSERVING = "OBSERVING"
    ANALYZING = "ANALYZING"
    WAITING = "WAITING"
    SIGNAL_READY = "SIGNAL_READY"
    RISK_CHECK = "RISK_CHECK"
    EXECUTING = "EXECUTING"
    POSITION_ACTIVE = "POSITION_ACTIVE"
    EXIT_PENDING = "EXIT_PENDING"
    REFLECTING = "REFLECTING"
    PAUSED = "PAUSED"
    EMERGENCY_STOP = "EMERGENCY_STOP"


class ExecutionMode(str, Enum):
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    ASSISTED_LIVE = "ASSISTED_LIVE"
    CONTROLLED_LIVE = "CONTROLLED_LIVE"


class HypothesisStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CONFIRMED = "CONFIRMED"
    WEAKENED = "WEAKENED"
    INVALIDATED = "INVALIDATED"


class RegimeClass(str, Enum):
    TRENDING_BULLISH = "TRENDING_BULLISH"
    TRENDING_BEARISH = "TRENDING_BEARISH"
    RANGE = "RANGE"
    VOL_EXPANSION = "VOL_EXPANSION"
    VOL_CONTRACTION = "VOL_CONTRACTION"
    UNKNOWN = "UNKNOWN"


class VolRegime(str, Enum):
    VOL_LOW = "VOL_LOW"
    VOL_NORMAL = "VOL_NORMAL"
    VOL_HIGH = "VOL_HIGH"
    VOL_EXPANSION = "VOL_EXPANSION"
    VOL_CONTRACTION = "VOL_CONTRACTION"
    VOL_UNKNOWN = "VOL_UNKNOWN"


class DataQuality(str, Enum):
    GOOD = "GOOD"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    MISSING = "MISSING"
    INVALID = "INVALID"


class RiskStatus(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ErrorClass(str, Enum):
    BEHAVIORAL = "BEHAVIORAL"
    EXECUTION = "EXECUTION"
    MODEL = "MODEL"
    DATA = "DATA"
    RISK = "RISK"
    NONE = "NONE"


class MemoryKind(str, Enum):
    EPISODIC = "EPISODIC"
    SEMANTIC = "SEMANTIC"
    ERROR = "ERROR"


class EvidenceKind(str, Enum):
    SUPPORTING = "SUPPORTING"
    COUNTER = "COUNTER"
    MISSING = "MISSING"


class ConditionKind(str, Enum):
    CONFIRMATION = "CONFIRMATION"
    INVALIDATION = "INVALIDATION"
