"""PARALLAX — memory architecture contracts (§13)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .enums import ErrorClass


@dataclass
class EpisodicRecord:
    id: str
    timestamp: datetime
    instrument: str
    decision_id: str = ""
    action: str = ""
    outcome: str = ""            # WIN | LOSS | SCRATCH | NO_TRADE
    pnl: float = 0.0
    decision_quality: Optional[float] = None   # 0..1
    regime: str = ""
    confidence: float = 0.0
    notes: str = ""


@dataclass
class SemanticRecord:
    id: str
    concept: str
    definition: str
    relevance: float = 1.0
    source: str = ""
    confidence: float = 1.0
    last_updated: Optional[datetime] = None


@dataclass
class ErrorRecord:
    id: str
    timestamp: datetime
    error_class: ErrorClass
    description: str
    decision_id: str = ""
    severity: str = "INFO"
    learned_rule: str = ""


@dataclass
class MemoryWeight:
    """Weights applied to a retrieved memory (§13.1): retrieved memories are
    evidence, not instructions."""
    relevance: float = 1.0
    recency: float = 1.0
    sample_size: float = 1.0
    confidence: float = 1.0

    def combined(self) -> float:
        return self.relevance * self.recency * self.sample_size * self.confidence
