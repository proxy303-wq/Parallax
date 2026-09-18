"""TypeSafe System One — the typed-judgment validator (NOT a chat LLM).

TypeSafe.ai System One (api.typesafe.ai) takes a structured state plus typed
questions (noul / choice / score) and returns *calibrated* answers: P(true),
a chosen label with a probability distribution, and a fractional score.  This
is a far better independent validator than a free-text LLM because its answers
are typed and calibration-aware.

Credentials are read from the gitignored .env (TYPESAFE_API_KEY), never logged,
and every failure returns None so the ensemble falls back gracefully.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .assessment import BrainAssessment
from .llm import mask

BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
SYSTEM_ONE_PATH = "/v1/systemone"


def _env(name: str) -> str:
    v = os.environ.get(name)
    if v:
        return v
    for path in (os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))), ".env"),
            r"C:\PrOxyTradingTerminal\.env", r"C:\Athena_X\.env"):
        if os.path.exists(path):
            for line in open(path, encoding="utf-8-sig"):
                line = line.strip()
                if "=" not in line or line.startswith("#"):
                    continue
                k, _, val = line.partition("=")
                if k.strip() == name:
                    return val.strip().strip('"').strip("'")
    return ""


def noul(instructions: str, true: str | None = None, false: str | None = None) -> dict:
    q = {"type": "noul", "instructions": instructions}
    crit = {k: v for k, v in (("true", true), ("false", false)) if v}
    if crit:
        q["criteria"] = crit
    return q


def choice(instructions: str, criteria: dict) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": dict(criteria)}


def score(instructions: str, criteria: list) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": list(criteria)}


class TypeSafeSystemOne:
    def __init__(self, api_key: str | None = None, model: str | None = None,
                 base_url: str | None = None, timeout: int = 25):
        self.api_key = api_key if api_key is not None else _env("TYPESAFE_API_KEY")
        self.model = model or _env("TYPESAFE_MODEL") or DEFAULT_MODEL
        self.base_url = (base_url or _env("TYPESAFE_API_BASE") or BASE_URL).rstrip("/")
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def masked(self) -> dict:
        return {"provider": "TypeSafeSystemOne", "endpoint": self.base_url,
                "model": self.model, "api_key": mask(self.api_key)}

    def _ask(self, state: dict, questions: dict) -> dict | None:
        if not self.enabled:
            return None
        body = json.dumps({"state": state, "model": self.model,
                           "questions": questions}).encode()
        req = urllib.request.Request(self.base_url + SYSTEM_ONE_PATH, data=body,
                                     method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + self.api_key)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            return None

    def judge(self, context: dict, proposed_direction: str = "flat") -> BrainAssessment | None:
        """Ask the typed trade-judgment questions; return a BrainAssessment."""
        state = dict(context)
        state["proposed_direction"] = proposed_direction
        questions = {
            "direction_correct": noul(
                "Will price move in the " + proposed_direction + " direction over the holding window?",
                true="The move in this direction is the more likely outcome",
                false="This direction is unlikely or the move is already spent"),
            "edge_sufficient": noul(
                "Is the expected edge large enough to clear transaction cost and justify the risk?",
                true="Edge clearly exceeds cost and risk",
                false="Edge is thin, negative, or already priced in"),
            "action": choice("What is the right action for this candidate?",
                             {"approve": "Enter the trade",
                              "reduce": "Enter at reduced size",
                              "reject": "Stand aside"}),
            "setup_quality": score("Rate the overall setup quality",
                                   ["poor", "weak", "fair", "good", "excellent"]),
        }
        payload = self._ask(state, questions)
        if not payload:
            return None
        answers = payload.get("answers") or {}

        action = (answers.get("action") or {}).get("choice")
        if action not in ("approve", "reduce", "reject"):
            action = "reject"
        p_correct = (answers.get("direction_correct") or {}).get("noul")
        p_edge = (answers.get("edge_sufficient") or {}).get("noul")
        try:
            conf = float(p_correct) if p_correct is not None else 0.5
        except (TypeError, ValueError):
            conf = 0.5
        conf = max(0.0, min(1.0, conf))
        quality = (answers.get("setup_quality") or {}).get("score")

        reasons = ["direction_correct=" + str(round(conf, 3)),
                   "edge_sufficient=" + str(p_edge),
                   "setup_quality=" + str(quality)]
        return BrainAssessment(
            direction=proposed_direction,
            confidence=round(conf, 3),
            verdict=action,
            thesis="TypeSafe System One typed judgment",
            counter_argument=("edge P=" + str(p_edge)) if p_edge is not None else "",
            uncertainty="calibrated" if quality is not None else "",
            reasons=reasons,
            llm_used=True,
            provider="TypeSafeSystemOne",
        )
