"""PARALLAX — reflection & learning loop contracts (§14)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .enums import ErrorClass


@dataclass
class ReflectionRecord:
    id: str
    timestamp: datetime
    decision_id: str
    outcome: str
    decision_quality_score: Optional[float] = None   # 0..1
    error_classification: ErrorClass = ErrorClass.NONE
    lessons: list[str] = field(default_factory=list)
    proposed_changes: list[str] = field(default_factory=list)  # NOT auto-applied

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "timestamp": self.timestamp.isoformat(),
            "decision_id": self.decision_id, "outcome": self.outcome,
            "decision_quality_score": self.decision_quality_score,
            "error_classification": self.error_classification.value,
            "lessons": self.lessons, "proposed_changes": self.proposed_changes,
        }
