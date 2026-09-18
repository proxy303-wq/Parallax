"""PARALLAX — hypothesis & scenario engine contracts (§9)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .enums import ConditionKind, Direction, EvidenceKind, HypothesisStatus


@dataclass
class EvidenceItem:
    id: str
    kind: EvidenceKind
    description: str
    weight: float = 1.0
    source: str = "perception"

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind.value,
                "description": self.description, "weight": self.weight,
                "source": self.source}


@dataclass
class Condition:
    id: str
    kind: ConditionKind
    description: str
    met: bool = False
    observed: bool = False


@dataclass
class CalibrationMetadata:
    bucket: str = ""                             # e.g. "0.60-0.70"
    sample_size: int = 0
    historical_hit_rate: Optional[float] = None


@dataclass
class Hypothesis:
    id: str
    direction: Direction
    thesis: str
    prior_estimate: Optional[float] = None
    evidence: list[EvidenceItem] = field(default_factory=list)
    counter_evidence: list[EvidenceItem] = field(default_factory=list)
    required_confirmations: list[Condition] = field(default_factory=list)
    invalidation_conditions: list[Condition] = field(default_factory=list)
    confidence: float = 0.0
    confidence_calibration: CalibrationMetadata = field(default_factory=CalibrationMetadata)
    expected_value: Optional[float] = None
    status: HypothesisStatus = HypothesisStatus.ACTIVE

    @property
    def confirmations_met(self) -> int:
        return sum(1 for c in self.required_confirmations if c.met)

    @property
    def invalidated(self) -> bool:
        return any(c.met for c in self.invalidation_conditions)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "direction": self.direction.value,
            "thesis": self.thesis, "confidence": self.confidence,
            "status": self.status.value,
            "evidence": [e.as_dict() for e in self.evidence],
            "counter_evidence": [e.as_dict() for e in self.counter_evidence],
            "expected_value": self.expected_value,
        }
