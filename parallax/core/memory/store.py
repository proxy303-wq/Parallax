"""Memory store (§13).

Episodic (trades), semantic (knowledge), and error memory.  Retrieved
memories are weighted by regime relevance, recency, sample size and
confidence (§13.1) — evidence, never instructions.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from parallax.contracts import (
    EpisodicRecord, ErrorRecord, MemoryWeight, SemanticRecord,
)


class MemoryStore:
    def __init__(self):
        self.episodic: list[EpisodicRecord] = []
        self.semantic: dict[str, SemanticRecord] = {}
        self.errors: list[ErrorRecord] = []

    # ---- writes ----------------------------------------------------------
    def record_episodic(self, rec: EpisodicRecord) -> None:
        self.episodic.append(rec)

    def record_error(self, rec: ErrorRecord) -> None:
        self.errors.append(rec)

    def add_semantic(self, rec: SemanticRecord) -> None:
        self.semantic[rec.concept] = rec

    # ---- reads -----------------------------------------------------------
    def recall_episodic(self, instrument: str | None = None,
                        regime: str | None = None,
                        k: int = 10) -> list[tuple[EpisodicRecord, MemoryWeight]]:
        now = datetime.now(timezone.utc)
        scored: list[tuple[EpisodicRecord, MemoryWeight]] = []
        for rec in self.episodic:
            if instrument and rec.instrument != instrument:
                continue
            if regime and rec.regime and rec.regime != regime:
                continue
            w = self._weight(rec, regime, now)
            scored.append((rec, w))
        scored.sort(key=lambda t: t[1].combined(), reverse=True)
        return scored[:k]

    def outcomes(self, instrument: str | None = None) -> list[str]:
        recs = self.episodic if instrument is None else [
            r for r in self.episodic if r.instrument == instrument]
        return [r.outcome for r in recs]

    def consecutive_losses(self, instrument: str | None = None) -> int:
        n = 0
        for o in reversed(self.outcomes(instrument)):
            if o == "LOSS":
                n += 1
            else:
                break
        return n

    def _weight(self, rec: EpisodicRecord, regime: str | None,
                now: datetime) -> MemoryWeight:
        relevance = 1.0 if (regime is None or rec.regime == regime) else 0.4
        age_days = 0.0
        if rec.timestamp.tzinfo is None:
            age_days = (now.replace(tzinfo=None) - rec.timestamp).total_seconds() / 86400.0
        else:
            age_days = (now - rec.timestamp).total_seconds() / 86400.0
        recency = math.exp(-age_days / 90.0)
        sample_size = min(len(self.episodic) / 20.0, 1.0) + 0.1
        confidence = rec.confidence if rec.confidence > 0 else 0.5
        return MemoryWeight(relevance=relevance, recency=recency,
                            sample_size=sample_size, confidence=confidence)
