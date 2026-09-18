"""Confidence calibration tracker (§8.2).

Stores predicted confidence alongside realized outcomes, then evaluates
calibration by probability buckets, hit rates and Brier score.  A 70% bucket
must not behave like a 45% bucket.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CalibrationTracker:
    buckets: dict[str, dict] = field(default_factory=dict)  # bucket -> {n, hits}
    brier_sum: float = 0.0
    brier_n: int = 0
    min_samples: int = 30

    @staticmethod
    def bucket_of(confidence: float) -> str:
        lo = int(confidence * 10) / 10
        return f"{lo:.1f}-{lo + 0.1:.1f}"

    def record(self, confidence: float, outcome: bool) -> None:
        b = self.bucket_of(confidence)
        cell = self.buckets.setdefault(b, {"n": 0, "hits": 0})
        cell["n"] += 1
        cell["hits"] += int(outcome)
        self.brier_sum += (confidence - int(outcome)) ** 2
        self.brier_n += 1

    def hit_rate(self, bucket: str) -> float | None:
        cell = self.buckets.get(bucket)
        if not cell or cell["n"] == 0:
            return None
        return cell["hits"] / cell["n"]

    def sample_size(self, bucket: str) -> int:
        cell = self.buckets.get(bucket)
        return cell["n"] if cell else 0

    def calibrate(self, confidence: float) -> float | None:
        """Shrink raw confidence toward the measured bucket hit rate.

        Returns None when the bucket has too few samples (caller falls back to
        the raw confidence).
        """
        b = self.bucket_of(confidence)
        rate = self.hit_rate(b)
        n = self.sample_size(b)
        if rate is None or n < self.min_samples:
            return None
        alpha = n / (n + self.min_samples)
        return alpha * rate + (1.0 - alpha) * confidence

    def brier_score(self) -> float | None:
        if self.brier_n == 0:
            return None
        return self.brier_sum / self.brier_n
