"""PARALLAX contracts — canonical data types shared by every module.

Import surface (ergonomic)::

    from parallax.contracts import MarketState, TradeDecision, RiskConfig
"""
from .enums import (
    Action, ConditionKind, DataQuality, DecisionClass, Direction, ErrorClass,
    EvidenceKind, ExecutionMode, HypothesisStatus, InstrumentType, Market,
    MemoryKind, OrderStatus, OrderType, RegimeClass, RiskStatus, Side,
    SystemMode, TimeInForce, VolRegime,
)
from .hypotheses import (
    CalibrationMetadata, Condition, EvidenceItem, Hypothesis,
)
from .decisions import RiskPlan, TradeDecision
from .ids import new_id, utc_now
from .market import (
    DataQualityState, IndicatorState, InstrumentId, LiquidityState, MarketEvent,
    MarketState, PriceState, RegimeState, SessionState, StructureState,
    TimeframeState, VolatilityState, VolumeState,
)
from .memory import (
    EpisodicRecord, ErrorRecord, MemoryWeight, SemanticRecord,
)
from .orders import (
    AccountState, BrokerReconciliation, CancelAck, ModifyAck, ModifyOrderRequest,
    Order, OrderAck, OrderIntent, Position, Quote, ValidatedOrderIntent,
)
from .reflection import ReflectionRecord
from .risk import RiskConfig, RiskVerdict
from .structure import (
    Bar, Break, BreakKind, LiquidityLevel, LiquiditySide, Swing, SwingKind,
    Zone, ZoneKind,
)
from .specs import SPECS, InstrumentSpec, spec_for
from .telemetry import AuditEvent
from .validate import ValidationError, validate_order_intent, validate_risk_config

__all__ = [
    "Action", "ConditionKind", "DataQuality", "DecisionClass", "Direction",
    "ErrorClass", "EvidenceKind", "ExecutionMode", "HypothesisStatus",
    "InstrumentType", "Market", "MemoryKind", "OrderStatus", "OrderType",
    "RegimeClass", "RiskStatus", "Side", "SystemMode", "TimeInForce",
    "VolRegime", "CalibrationMetadata", "Condition", "EvidenceItem",
    "Hypothesis", "RiskPlan", "TradeDecision", "new_id", "utc_now",
    "DataQualityState", "IndicatorState", "InstrumentId", "LiquidityState",
    "MarketEvent", "MarketState", "PriceState", "RegimeState", "SessionState",
    "StructureState", "TimeframeState", "VolatilityState", "VolumeState",
    "EpisodicRecord", "ErrorRecord", "MemoryWeight", "SemanticRecord",
    "AccountState", "BrokerReconciliation", "CancelAck", "ModifyAck",
    "ModifyOrderRequest", "Order", "OrderAck", "OrderIntent", "Position",
    "Quote", "ValidatedOrderIntent", "ReflectionRecord",
    "RiskConfig", "RiskVerdict", "Bar", "Break", "BreakKind", "LiquidityLevel",
    "LiquiditySide", "Swing", "SwingKind", "Zone", "ZoneKind", "AuditEvent",
    "ValidationError", "validate_order_intent", "validate_risk_config",
    "SPECS", "InstrumentSpec", "spec_for",
]
