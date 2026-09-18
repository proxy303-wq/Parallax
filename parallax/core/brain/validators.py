"""Multi-validator ensemble — the "validate with other validators" gate.

A candidate trade must survive independent validators before it reaches the
risk gate: (1) the deterministic confirmation/regime/edge checks already inside
the decision engine, (2) DeepSeek (free-text reasoning), and (3) TypeSafe
System One (typed, calibrated judgment).  The ensemble takes the most
conservative verdict and the minimum confidence across providers.
"""
from __future__ import annotations

from dataclasses import dataclass

from parallax.contracts import (
    Action, DecisionClass, Hypothesis, MarketState, TradeDecision,
)

from .assessment import BrainAssessment
from .brain import Brain, build_state_context, combine_assessments
from .typesafe import TypeSafeSystemOne


@dataclass
class ValidationResult:
    verdict: str                 # approve | reduce | reject
    assessment: BrainAssessment
    allowed: bool

    def as_dict(self) -> dict:
        return {"verdict": self.verdict,
                "assessment": self.assessment.as_dict(),
                "allowed": self.allowed}


class ValidatorEnsemble:
    def __init__(self, brain: Brain | None = None,
                 typesafe: TypeSafeSystemOne | None = None,
                 reduce_confidence_floor: float = 0.62):
        self.brain = brain or Brain()
        self.typesafe = typesafe
        self.reduce_confidence_floor = reduce_confidence_floor

    def providers(self) -> list:
        out = list(self.brain.providers())
        if self.typesafe is not None:
            out.append(self.typesafe.masked())
        return out

    def validate(self, decision: TradeDecision, state: MarketState,
                 hypotheses: list[Hypothesis],
                 recent_outcomes: list[str] | None = None) -> ValidationResult | None:
        """Validate a candidate trade.  Returns None when there is nothing to
        validate (decision is not a TRADE)."""
        if decision.decision_class != DecisionClass.TRADE:
            return None

        assessments: list[BrainAssessment] = []
        assessments.append(self.brain.think(state, hypotheses, recent_outcomes))

        if self.typesafe is not None and self.typesafe.enabled:
            proposed = "long" if decision.action == Action.BUY else "short"
            ctx = build_state_context(state, hypotheses, recent_outcomes)
            t = self.typesafe.judge(ctx, proposed_direction=proposed)
            if t is not None:
                assessments.append(t)

        combined = combine_assessments(assessments)

        # the ensemble can only downgrade, never upgrade
        if combined.verdict == "reject":
            verdict = "reject"
        elif combined.verdict == "reduce" and decision.confidence < self.reduce_confidence_floor:
            verdict = "reject"
        else:
            verdict = "approve"

        if combined.direction == "flat" and decision.confidence < 0.7:
            verdict = "reject"

        allowed = verdict == "approve"
        return ValidationResult(verdict, combined, allowed)
