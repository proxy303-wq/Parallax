"""Metacognition engine — pre-decision self-audit (§8.1).

Evaluates not only the market thesis but the quality of the reasoning itself.
Returns a structured SelfAudit plus a confidence adjustment and any fired
bias flags.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from parallax.contracts import Hypothesis, MarketState

from .biases import BiasDetector, BiasFlag
from .calibration import CalibrationTracker


@dataclass
class SelfAudit:
    claim: str = ""
    supporting: int = 0
    contradicting: int = 0
    missing_evidence: list[str] = field(default_factory=list)
    double_counting: list[str] = field(default_factory=list)
    alternative: str = ""
    invalidation: str = ""
    regime_seen_before: bool = False
    calibrated: bool = False
    action_bias: bool = False
    reward_sufficient: bool = False
    bias_flags: list[BiasFlag] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "claim": self.claim,
            "supporting": self.supporting,
            "contradicting": self.contradicting,
            "missing_evidence": self.missing_evidence,
            "double_counting": self.double_counting,
            "alternative": self.alternative,
            "invalidation": self.invalidation,
            "regime_seen_before": self.regime_seen_before,
            "calibrated": self.calibrated,
            "action_bias": self.action_bias,
            "reward_sufficient": self.reward_sufficient,
            "bias_flags": [f.name for f in self.bias_flags if f.triggered],
        }


class MetacognitionEngine:
    def __init__(self, calibration: CalibrationTracker | None = None):
        self.calibration = calibration or CalibrationTracker()
        self.bias_detector = BiasDetector()

    def audit(self, hypothesis: Hypothesis, state: MarketState,
              reward_risk: float = 2.0, recent_outcomes: list[str] | None = None,
              trades_today: int = 0) -> SelfAudit:
        recent_outcomes = recent_outcomes or []
        a = SelfAudit()
        a.claim = hypothesis.thesis
        a.supporting = len(hypothesis.evidence)
        a.contradicting = len(hypothesis.counter_evidence)

        # missing evidence: trend confirmation + displacement + MSS
        missing = []
        if state.indicators.adx < 20:
            missing.append("no confirmed trend (ADX < 20)")
        if state.structure.last_mss is None:
            missing.append("no change-of-character yet")
        if not hypothesis.required_confirmations:
            missing.append("no required confirmations defined")
        a.missing_evidence = missing

        # double-counting: EMA alignment + trend + regime are correlated
        a.double_counting = [
            "trend, EMA alignment and regime are correlated (counted separately)"
        ]

        a.alternative = ("Range mean-reversion" if hypothesis.direction.value != "NEUTRAL"
                         else "Directional continuation")
        inv = hypothesis.invalidation_conditions
        a.invalidation = inv[0].description if inv else "none defined"

        a.regime_seen_before = state.regime.regime.value != "UNKNOWN"
        a.calibrated = hypothesis.confidence_calibration.sample_size >= 30
        a.action_bias = (hypothesis.direction.value == "NEUTRAL" and
                         hypothesis.confidence < 0.5)

        a.reward_sufficient = reward_risk >= 2.0

        calibrated = self.calibration.calibrate(hypothesis.confidence)
        a.bias_flags = self.bias_detector.check(
            hypothesis.confidence, recent_outcomes, trades_today,
            state.indicators.rsi, state.indicators.adx,
            len(hypothesis.counter_evidence),
            hypothesis.confidence_calibration.sample_size)
        return a

    def calibrated_confidence(self, confidence: float) -> float | None:
        return self.calibration.calibrate(confidence)
