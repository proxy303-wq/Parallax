"""Persistent brain memory — "remembers the entire end-to-end process".

Accumulates the brain's assessments, outcomes, lessons and regime history to a
JSON file, so the brain resumes with context after a restart.  Retrieved
memories are context for the prompt — evidence, never instructions.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BrainMemory:
    def __init__(self, path: str):
        self.path = path
        self.data = {"assessments": [], "lessons": [], "outcomes": [],
                     "regime_history": []}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
                if isinstance(loaded, dict):
                    self.data.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass

    def save(self) -> None:
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, default=str)

    def remember_assessment(self, assessment: dict, outcome: str | None = None) -> None:
        entry = {"ts": _now(), "assessment": assessment, "outcome": outcome}
        self.data["assessments"].append(entry)
        if outcome:
            self.data["outcomes"].append({
                "ts": entry["ts"], "outcome": outcome,
                "direction": assessment.get("direction"),
                "verdict": assessment.get("verdict")})
        self.data["assessments"] = self.data["assessments"][-200:]
        self.data["outcomes"] = self.data["outcomes"][-500:]
        self.save()

    def add_lesson(self, lesson: str) -> None:
        self.data["lessons"].append({"ts": _now(), "lesson": lesson})
        self.data["lessons"] = self.data["lessons"][-100:]
        self.save()

    def record_regime(self, regime: str) -> None:
        self.data["regime_history"].append({"ts": _now(), "regime": regime})
        self.data["regime_history"] = self.data["regime_history"][-500:]

    def record_outcome(self, outcome: str, direction: str | None = None) -> None:
        self.data["outcomes"].append({"ts": _now(), "outcome": outcome,
                                      "direction": direction})
        self.data["outcomes"] = self.data["outcomes"][-500:]
        self.save()

    def recent(self, k: int = 15) -> list:
        return [e["assessment"] for e in self.data["assessments"][-k:]]

    def lessons(self) -> list:
        return [e["lesson"] for e in self.data["lessons"]]

    def outcome_counts(self) -> dict:
        out = {}
        for o in self.data["outcomes"]:
            out[o["outcome"]] = out.get(o["outcome"], 0) + 1
        return out
