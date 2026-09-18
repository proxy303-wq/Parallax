"""Trading knowledge base (semantic memory seed).

A deterministic, queryable corpus of ICT/SMC and risk concepts.  The design
doc calls for a "trading knowledge base implemented"; this is the machine-
readable form the reasoning engine references.  Retrieved knowledge is
evidence for the reasoning layer — never a direct order.
"""
from __future__ import annotations

from parallax.contracts import SemanticRecord

SEED_CONCEPTS: list[dict] = [
    {"concept": "FVG", "definition": "Fair value gap: a three-candle imbalance where the third candle does not overlap the first, leaving a void that price often revisits.", "relevance": 0.9, "source": "ICT/SMC"},
    {"concept": "OB", "definition": "Order block: the last opposing candle before a displacement move; a zone where institutional orders likely rest.", "relevance": 0.9, "source": "ICT/SMC"},
    {"concept": "BOS", "definition": "Break of structure: price breaks a prior swing in the direction of the prevailing trend (continuation).", "relevance": 0.9, "source": "ICT/SMC"},
    {"concept": "CHoCH", "definition": "Change of character / market structure shift: price breaks a prior swing against the prevailing trend (potential reversal).", "relevance": 0.9, "source": "ICT/SMC"},
    {"concept": "LIQUIDITY_SWEEP", "definition": "A sharp move through an obvious liquidity level (equal highs/lows, session high/low) that reverses; often the precursor to a displacement.", "relevance": 0.85, "source": "ICT/SMC"},
    {"concept": "DISPLACEMENT", "definition": "A strong, high-momentum directional candle that leaves imbalance (an FVG); it signals institutional intent.", "relevance": 0.85, "source": "ICT/SMC"},
    {"concept": "POI", "definition": "Point of interest: a confluence zone (order block / FVG / liquidity) where a trade may be taken with confirmation.", "relevance": 0.8, "source": "ICT/SMC"},
    {"concept": "HTF_ALIGNMENT", "definition": "Higher-timeframe bias aligning with the lower-timeframe entry direction increases setup quality.", "relevance": 0.8, "source": "ICT/SMC"},
    {"concept": "KILLZONE", "definition": "A session window of elevated institutional activity (open drive / London / New York); entries are filtered to high-quality killzones.", "relevance": 0.7, "source": "ICT/SMC"},
    {"concept": "RISK_PER_TRADE", "definition": "The fixed fraction of equity risked on any single trade (hard cap, e.g. 0.5%).", "relevance": 1.0, "source": "risk"},
    {"concept": "DAILY_LOSS_LIMIT", "definition": "Maximum permitted loss in one session; hitting it halts trading for the day.", "relevance": 1.0, "source": "risk"},
    {"concept": "RR_RATIO", "definition": "Reward-to-risk ratio; the target distance divided by the stop distance. Minimum threshold is enforced by the risk gate.", "relevance": 1.0, "source": "risk"},
    {"concept": "NO_TRADE", "definition": "A first-class decision: when no validated edge exists, the correct action is to do nothing.", "relevance": 1.0, "source": "discipline"},
    {"concept": "CONFIRMATION", "definition": "Evidence required before entry, e.g. liquidity sweep followed by displacement and MSS.", "relevance": 0.8, "source": "ICT/SMC"},
    {"concept": "INVALIDATION", "definition": "The price level or condition that proves a thesis wrong; the stop or exit trigger.", "relevance": 0.9, "source": "risk"},
]


class KnowledgeBase:
    """A small semantic store with keyword retrieval."""

    def __init__(self, concepts: list[dict] | None = None):
        self._records: dict[str, SemanticRecord] = {}
        for c in (concepts or SEED_CONCEPTS):
            rec = SemanticRecord(
                id="sem_" + c["concept"],
                concept=c["concept"],
                definition=c["definition"],
                relevance=float(c.get("relevance", 1.0)),
                source=c.get("source", ""),
                confidence=float(c.get("confidence", 1.0)),
            )
            self._records[rec.concept] = rec

    def get(self, concept: str) -> SemanticRecord | None:
        return self._records.get(concept)

    def search(self, query: str) -> list[SemanticRecord]:
        q = query.lower()
        out = [r for r in self._records.values()
               if q in r.concept.lower() or q in r.definition.lower()]
        out.sort(key=lambda r: -r.relevance)
        return out

    def add(self, record: SemanticRecord) -> None:
        self._records[record.concept] = record

    def all(self) -> list[SemanticRecord]:
        return list(self._records.values())
