"""Post-trade reflection — decision-quality audit & error classification (§14).

Produces structured learning records.  It NEVER rewrites production logic:
proposed changes go to the promotion gate, not straight into live code.
"""
from __future__ import annotations

from parallax.contracts import (
    ErrorClass, ReflectionRecord, TradeDecision, new_id,
)


class ReflectionEngine:
    def reflect(self, decision: TradeDecision, outcome: str, pnl: float,
                regime: str, bias_flags: list[str] | None = None,
                data_quality: str = "GOOD") -> ReflectionRecord:
        bias_flags = bias_flags or []
        quality = self._quality_score(decision, outcome, pnl, bias_flags)
        error_class = self._classify(decision, outcome, pnl, bias_flags, data_quality)
        lessons = self._lessons(decision, outcome, quality, error_class)
        proposed = self._proposals(error_class, bias_flags)

        return ReflectionRecord(
            id=new_id("ref", decision.timestamp),
            timestamp=decision.timestamp,
            decision_id=decision.decision_id,
            outcome=outcome,
            decision_quality_score=quality,
            error_classification=error_class,
            lessons=lessons,
            proposed_changes=proposed,
        )

    @staticmethod
    def _quality_score(decision: TradeDecision, outcome: str, pnl: float,
                       bias_flags: list[str]) -> float:
        cls = decision.decision_class.value
        if cls == "WAIT":
            return 0.9  # disciplined no-trade is high quality by default
        if cls == "ABORT":
            return 0.7
        if outcome == "WIN":
            base = 0.85
        elif outcome == "SCRATCH":
            base = 0.7
        else:  # LOSS
            base = 0.45  # a disciplined loss still has value
        # behavioural flags hurt decision quality
        base -= 0.15 * len(bias_flags)
        return round(max(0.0, min(1.0, base)), 2)

    @staticmethod
    def _classify(decision: TradeDecision, outcome: str, pnl: float,
                  bias_flags: list[str], data_quality: str) -> ErrorClass:
        if data_quality != "GOOD":
            return ErrorClass.DATA
        if bias_flags:
            return ErrorClass.BEHAVIORAL
        if outcome == "LOSS":
            return ErrorClass.MODEL  # thesis was wrong
        return ErrorClass.NONE

    @staticmethod
    def _lessons(decision: TradeDecision, outcome: str, quality: float,
                 error_class: ErrorClass) -> list[str]:
        lessons = []
        if outcome == "LOSS":
            lessons.append("Thesis invalidated; review invalidation placement.")
        if decision.decision_class.value == "WAIT":
            lessons.append("Standing aside preserved capital.")
        if error_class == ErrorClass.BEHAVIORAL:
            lessons.append("A behavioural bias was present; tighten the firewall.")
        return lessons

    @staticmethod
    def _proposals(error_class: ErrorClass, bias_flags: list[str]) -> list[str]:
        proposals = []
        if error_class == ErrorClass.BEHAVIORAL:
            proposals.append("candidate: strengthen bias firewall threshold")
        if error_class == ErrorClass.MODEL:
            proposals.append("candidate: revisit setup confirmation rules")
        return proposals
