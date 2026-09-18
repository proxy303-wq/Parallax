"""Deterministic regime classification.

Interpretation of perception facts into a RegimeState.  Pure function of the
MarketState's indicator and structure fields — no randomness, no I/O.
"""
from __future__ import annotations

from parallax.contracts import MarketState, RegimeClass, RegimeState, VolRegime


def classify_regime(state: MarketState) -> RegimeState:
    ind = state.indicators
    adx = ind.adx
    ema_fast = ind.ema_fast
    ema_slow = ind.ema_slow
    trend = state.structure.trend
    vol_pct = state.volatility.vol_percentile

    # volatility regime from trailing percentile
    if vol_pct is None:
        vol_regime = VolRegime.VOL_UNKNOWN
    elif vol_pct <= 0.33:
        vol_regime = VolRegime.VOL_LOW
    elif vol_pct >= 0.67:
        vol_regime = VolRegime.VOL_HIGH
    else:
        vol_regime = VolRegime.VOL_NORMAL

    # trend direction requires trend confirmation (ADX) + EMA alignment
    trend_direction = "NEUTRAL"
    if adx >= 20.0:
        if ema_fast > ema_slow and trend != "BEARISH":
            trend_direction = "BULLISH"
        elif ema_fast < ema_slow and trend != "BULLISH":
            trend_direction = "BEARISH"

    if trend_direction == "BULLISH":
        regime = RegimeClass.TRENDING_BULLISH
    elif trend_direction == "BEARISH":
        regime = RegimeClass.TRENDING_BEARISH
    elif vol_pct is not None and vol_pct >= 0.67:
        regime = RegimeClass.VOL_EXPANSION
    elif vol_pct is not None and vol_pct <= 0.33:
        regime = RegimeClass.VOL_CONTRACTION
    else:
        regime = RegimeClass.RANGE

    return RegimeState(
        regime=regime,
        trend_direction=trend_direction,
        volatility_regime=vol_regime,
        session_bias=trend_direction,
    )
