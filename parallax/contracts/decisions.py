"""PARALLAX — trade decision contract (§10.1)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .enums import Action, DecisionClass
from .hypotheses import Condition


@dataclass
class RiskPlan:
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    target: Optional[float] = None
    risk_amount: float = 0.0                     # currency risk
    risk_pct: float = 0.0                        # % of equity
    position_size: int = 0
    reward_risk: Optional[float] = None
    invalidation_level: Optional[float] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_price": self.entry_price, "stop_loss": self.stop_loss,
            "target": self.target, "risk_amount": self.risk_amount,
            "risk_pct": self.risk_pct, "position_size": self.position_size,
            "reward_risk": self.reward_risk,
            "invalidation_level": self.invalidation_level,
        }


@dataclass
class TradeDecision:
    decision_id: str
    timestamp: datetime
    instrument: str
    action: Action
    decision_class: DecisionClass
    setup_id: str = ""
    thesis: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    counter_evidence_ids: list[str] = field(default_factory=list)
    invalidation: list[Condition] = field(default_factory=list)
    expected_value: float = 0.0
    confidence: float = 0.0
    calibrated_confidence: Optional[float] = None
    risk_plan: RiskPlan = field(default_factory=RiskPlan)
    model_versions: dict[str, str] = field(default_factory=dict)
    reasoning_trace_id: str = ""
    reasons: list[str] = field(default_factory=list)

    @property
    def is_trade(self) -> bool:
        return self.decision_class == DecisionClass.TRADE

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "timestamp": self.timestamp.isoformat(),
            "instrument": self.instrument,
            "action": self.action.value,
            "decision_class": self.decision_class.value,
            "setup_id": self.setup_id,
            "thesis": self.thesis,
            "expected_value": self.expected_value,
            "confidence": self.confidence,
            "calibrated_confidence": self.calibrated_confidence,
            "risk_plan": self.risk_plan.as_dict(),
            "reasons": self.reasons,
        }
