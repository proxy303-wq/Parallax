"""Typed contract for a brain assessment (advisory, never an order)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BrainAssessment:
    direction: str          # long | short | flat
    confidence: float       # 0..1
    verdict: str            # approve | reduce | reject
    thesis: str = ""
    counter_argument: str = ""
    uncertainty: str = ""
    reasons: list = field(default_factory=list)
    llm_used: bool = False
    provider: str = ""

    def as_dict(self) -> dict:
        return {
            "direction": self.direction,
            "confidence": round(self.confidence, 3),
            "verdict": self.verdict,
            "thesis": self.thesis,
            "counter_argument": self.counter_argument,
            "uncertainty": self.uncertainty,
            "reasons": self.reasons,
            "llm_used": self.llm_used,
            "provider": self.provider,
        }
