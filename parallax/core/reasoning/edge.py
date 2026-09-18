"""Statistical edge and expected-value computation.

Deterministic, calibration-aware.  The decision engine compares the resulting
expected value against a threshold; the risk gate then vets the plan.
"""
from __future__ import annotations

import math


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def softmax(logits: list[float]) -> list[float]:
    m = max(logits)
    exps = [math.exp(x - m) for x in logits]
    s = sum(exps)
    return [e / s for e in exps]


def expected_value(p_win: float, reward_risk: float) -> float:
    """Expected value in units of risk per trade.

    EV = p_win * RR - (1 - p_win).  Break-even when p_win = 1/(1+RR).
    """
    return p_win * reward_risk - (1.0 - p_win)


def calibrate_confidence(confidence: float, historical_hit_rate: float | None,
                         sample_size: int = 0, min_samples: int = 30) -> float | None:
    """Shrink a raw confidence toward a measured historical hit rate.

    Returns None when there is not enough calibration data.  With enough
    samples the calibrated value is a shrinkage blend that converges to the
    historical hit rate; with none it returns None (caller falls back to raw).
    """
    if historical_hit_rate is None or sample_size < min_samples:
        return None
    alpha = min(sample_size / (sample_size + min_samples), 1.0)
    return alpha * historical_hit_rate + (1.0 - alpha) * confidence


def break_even_win_rate(reward_risk: float) -> float:
    return 1.0 / (1.0 + reward_risk)
