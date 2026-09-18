"""Bias firewall (§8.3).

Detects the named behavioural biases before a decision is taken.  Flags are
either *blocking* (force a WAIT) or *downgrading* (reduce confidence).  They
never raise confidence.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BiasFlag:
    name: str
    description: str
    triggered: bool = False
    blocking: bool = False


class BiasDetector:
    def check(self, confidence: float, recent_outcomes: list[str],
              trades_today: int, rsi: float, adx: float,
              counter_evidence_count: int, calibration_samples: int,
              max_trades_per_day: int = 4) -> list[BiasFlag]:
        flags: list[BiasFlag] = []

        # revenge trading — blocking
        consecutive_losses = 0
        for o in reversed(recent_outcomes):
            if o == "LOSS":
                consecutive_losses += 1
            else:
                break
        flags.append(BiasFlag(
            "revenge", "Trading to recover a recent loss",
            triggered=consecutive_losses >= 2, blocking=True))

        # FOMO / chasing — blocking
        flags.append(BiasFlag(
            "fomo", "Entering an extended move without confirmation",
            triggered=(rsi > 72 or rsi < 28) and adx < 20, blocking=True))

        # confirmation bias (ignoring counter-evidence) — downgrading
        flags.append(BiasFlag(
            "confirmation", "Strong counter-evidence present while confidence stays high",
            triggered=counter_evidence_count >= 3 and confidence > 0.7, blocking=False))

        # overconfidence — downgrading
        flags.append(BiasFlag(
            "overconfidence", "High confidence with little calibration data",
            triggered=confidence > 0.9 and calibration_samples < 10, blocking=False))

        # trade-frequency pressure — blocking
        flags.append(BiasFlag(
            "frequency", "Trading too often in a session",
            triggered=trades_today >= max_trades_per_day, blocking=True))

        return flags

    def triggered(self, flags: list[BiasFlag]) -> list[BiasFlag]:
        return [f for f in flags if f.triggered]
