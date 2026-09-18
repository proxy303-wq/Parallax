"""Multi-timeframe context assembly.

Aggregates several timeframe MarketStates into one decision-ready primary
state: it fills the regime and sets the higher-timeframe structural bias.
"""
from __future__ import annotations

from parallax.contracts import MarketState

from .regime import classify_regime

_TF_ORDER = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]


def multi_timeframe_bias(states: dict[str, MarketState], primary: str) -> str:
    """Higher-timeframe structural bias for the primary timeframe."""
    primary_idx = _TF_ORDER.index(primary) if primary in _TF_ORDER else 0
    # consider all timeframes at or above the primary, in ascending order
    for tf in _TF_ORDER[primary_idx:]:
        st = states.get(tf)
        if st is not None and st.structure.trend != "NEUTRAL":
            return st.structure.trend
    return "NEUTRAL"


def build_context(states: dict[str, MarketState], primary: str = "5m") -> MarketState:
    """Return the primary MarketState enriched with regime and HTF bias."""
    if primary not in states:
        raise ValueError(f"primary timeframe '{primary}' not in states")
    primary_state = states[primary]
    primary_state.regime = classify_regime(primary_state)
    primary_state.structure.bias = multi_timeframe_bias(states, primary)
    return primary_state
