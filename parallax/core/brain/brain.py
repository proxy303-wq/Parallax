"""The Brain — DeepSeek reasons over structured state, with a deterministic fallback.

The prompt is built exclusively from structured system state (MarketState,
hypotheses, recent memory) — never credentials, never raw broker payloads.  The
brain is advisory/veto-only: it may approve, reduce or reject a candidate, but
it can never create or size an order.  Any LLM failure falls back to a
deterministic assessment so the system keeps working.
"""
from __future__ import annotations

import json

from parallax.contracts import Direction, Hypothesis, MarketState

from .assessment import BrainAssessment
from .llm import DeepSeekClient

SYSTEM_PROMPT = (
    "You are the reasoning brain of PARALLAX, an autonomous trading system. "
    "You are given a structured market snapshot and competing hypotheses. "
    "Act as a disciplined professional trader: distinguish observation from "
    "interpretation, weigh evidence against counter-evidence, quantify "
    "uncertainty, and prefer NO TRADE when the edge is insufficient. "
    "You are advisory only: you never place orders and never override hard risk "
    "limits. Return STRICT JSON only, with exactly these keys:\n"
    '{"direction": "long"|"short"|"flat", "confidence": 0.0-1.0, '
    '"verdict": "approve"|"reduce"|"reject", "thesis": "...", '
    '"counter_argument": "...", "uncertainty": "...", "reasons": ["..."]}'
)


_VERDICT_ORDER = {"approve": 0, "reduce": 1, "reject": 2}


def build_state_context(state: MarketState, hypotheses: list[Hypothesis],
                         recent_outcomes: list[str] | None) -> dict:
    """Structured, secret-free context shared by every validator."""
    ind = state.indicators
    return {
        "instrument": str(state.instrument),
        "price": state.last_price,
        "regime": state.regime.regime.value,
        "trend": state.structure.trend,
        "bias": state.structure.bias,
        "rsi": round(ind.rsi, 1),
        "adx": round(ind.adx, 1),
        "atr": round(ind.atr, 2) if ind.atr else None,
        "ema_fast": round(ind.ema_fast, 1),
        "ema_slow": round(ind.ema_slow, 1),
        "vwap": round(ind.vwap, 1) if ind.vwap else None,
        "opening_low": state.session.opening_low,
        "opening_high": state.session.opening_high,
        "swept_opening_low": state.structure.swept_opening_low,
        "swept_opening_high": state.structure.swept_opening_high,
        "nearest_sell_side": state.liquidity.nearest_sell_side,
        "nearest_buy_side": state.liquidity.nearest_buy_side,
        "data_quality": state.data_quality.status.value,
        "hypotheses": [h.as_dict() for h in hypotheses[:5]],
        "recent_outcomes": (recent_outcomes or [])[-10:],
    }


def combine_assessments(assessments: list[BrainAssessment]) -> BrainAssessment:
    """Combine independent validators conservatively: the most cautious verdict
    wins and confidence is the minimum (never averaged up)."""
    decisive = max(assessments, key=lambda a: _VERDICT_ORDER[a.verdict])
    verdict = decisive.verdict
    confidence = min(a.confidence for a in assessments)
    dirs = [a.direction for a in assessments]
    direction = max(set(dirs), key=dirs.count)
    reasons = []
    for a in assessments:
        tag = a.provider or "provider"
        reasons.append(tag + ": " + a.verdict + (" - " + a.reasons[0] if a.reasons else ""))
    return BrainAssessment(
        direction=direction, confidence=round(confidence, 3), verdict=verdict,
        thesis=decisive.thesis, counter_argument=decisive.counter_argument,
        uncertainty=decisive.uncertainty, reasons=reasons, llm_used=True,
        provider="ensemble(" + ",".join(a.provider or "?" for a in assessments) + ")",
    )


class Brain:
    def __init__(self, llm=None, clients=None, memory=None):
        if clients is not None:
            self.clients = list(clients)
        elif llm is not None:
            self.clients = [llm]
        else:
            self.clients = [DeepSeekClient()]
        self.llm = self.clients[0] if self.clients else DeepSeekClient()
        self.memory = memory

    def providers(self) -> list:
        return [c.masked() for c in self.clients]

    def think(self, state: MarketState, hypotheses: list[Hypothesis],
              recent_outcomes: list[str] | None = None) -> BrainAssessment:
        context = self._context(state, hypotheses, recent_outcomes)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",
             "content": json.dumps(context, default=str)
                        + "\n\nReturn strict JSON only."},
        ]
        assessments: list[BrainAssessment] = []
        for client in self.clients:
            try:
                data = client.complete_json(messages)
            except Exception:
                data = None
            if data:
                parsed = self._parse(data)
                if parsed is not None:
                    parsed.llm_used = True
                    parsed.provider = getattr(client, "name", "provider")
                    assessments.append(parsed)
        if assessments:
            return self._ensemble(assessments)
        return self._deterministic(state, hypotheses)

    @staticmethod
    def _ensemble(assessments: list[BrainAssessment]) -> BrainAssessment:
        return combine_assessments(assessments)

    # -- prompt context (structured, secret-free) -------------------------
    def _context(self, state: MarketState, hypotheses: list[Hypothesis],
                 recent_outcomes: list[str] | None) -> dict:
        ctx = build_state_context(state, hypotheses, recent_outcomes)
        if self.memory is not None:
            ctx["memory"] = self.memory.recent()[-12:]
        return ctx

    # -- parse + validate the LLM's structured answer ----------------------
    @staticmethod
    def _parse(data: dict) -> BrainAssessment | None:
        direction = data.get("direction")
        verdict = data.get("verdict")
        if direction not in ("long", "short", "flat"):
            return None
        if verdict not in ("approve", "reduce", "reject"):
            return None
        try:
            conf = float(data.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.5
        conf = max(0.0, min(1.0, conf))
        return BrainAssessment(
            direction=direction,
            confidence=round(conf, 3),
            verdict=verdict,
            thesis=str(data.get("thesis", ""))[:2000],
            counter_argument=str(data.get("counter_argument", ""))[:2000],
            uncertainty=str(data.get("uncertainty", ""))[:500],
            reasons=[str(r) for r in (data.get("reasons") or [])][:20],
        )

    # -- deterministic fallback (mirrors the decision engine) --------------
    @staticmethod
    def _deterministic(state: MarketState,
                       hypotheses: list[Hypothesis]) -> BrainAssessment:
        directional = [h for h in hypotheses
                       if h.direction in (Direction.LONG, Direction.SHORT)]
        if not directional:
            return BrainAssessment("flat", 0.5, "reject",
                                   "no directional hypothesis", "", "",
                                   ["no directional hypothesis"])
        best = max(directional, key=lambda h: h.confidence)
        conf = best.confidence
        verdict = "approve" if conf >= 0.6 else ("reduce" if conf >= 0.45 else "reject")
        return BrainAssessment(
            direction="long" if best.direction == Direction.LONG else "short",
            confidence=round(conf, 3),
            verdict=verdict,
            thesis=best.thesis,
            counter_argument="; ".join(e.description for e in best.counter_evidence[:3]),
            uncertainty="calibration pending" if conf > 0.8 else "moderate",
            reasons=["deterministic fallback (LLM unavailable)"],
        )
