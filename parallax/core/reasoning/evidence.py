"""Evidence gathering — turn perception facts into weighted evidence.

Every item is an *observation-derived* claim with a weight and a source.  The
hypothesis engine attaches these to hypotheses as supporting or counter
evidence; nothing here is a decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from parallax.contracts import (
    EvidenceItem, EvidenceKind, MarketState, RegimeClass,
)


@dataclass
class EvidenceBundle:
    bullish: list[EvidenceItem] = field(default_factory=list)
    bearish: list[EvidenceItem] = field(default_factory=list)

    @property
    def bull_weight(self) -> float:
        return sum(e.weight for e in self.bullish)

    @property
    def bear_weight(self) -> float:
        return sum(e.weight for e in self.bearish)

    @property
    def net(self) -> float:
        return self.bull_weight - self.bear_weight


def gather_evidence(state: MarketState) -> EvidenceBundle:
    """Weighted directional evidence from a single MarketState."""
    b = EvidenceBundle()
    ind = state.indicators
    price = state.last_price or 0.0
    regime = state.regime.regime

    def bull(desc: str, w: float = 1.0, src: str = "perception"):
        b.bullish.append(EvidenceItem(f"ev_bull_{len(b.bullish)}", EvidenceKind.SUPPORTING, desc, w, src))

    def bear(desc: str, w: float = 1.0, src: str = "perception"):
        b.bearish.append(EvidenceItem(f"ev_bear_{len(b.bearish)}", EvidenceKind.SUPPORTING, desc, w, src))

    # structure trend
    if state.structure.trend == "BULLISH":
        bull("Structure trend is bullish", 1.6, "structure")
    elif state.structure.trend == "BEARISH":
        bear("Structure trend is bearish", 1.6, "structure")

    # regime
    if regime == RegimeClass.TRENDING_BULLISH:
        bull("Regime classified trending-bullish", 1.4, "context")
    elif regime == RegimeClass.TRENDING_BEARISH:
        bear("Regime classified trending-bearish", 1.4, "context")
    elif regime in (RegimeClass.RANGE, RegimeClass.VOL_CONTRACTION):
        bull("Range/contraction regime favours mean reversion", 0.4, "context")
        bear("Range/contraction regime favours mean reversion", 0.4, "context")

    # EMA alignment
    if ind.ema_fast > ind.ema_slow:
        bull("Fast EMA above slow EMA", 1.0, "indicator")
    elif ind.ema_fast < ind.ema_slow:
        bear("Fast EMA below slow EMA", 1.0, "indicator")

    # trend strength
    if ind.adx >= 20.0:
        if state.structure.trend == "BULLISH":
            bull(f"ADX {ind.adx:.0f} confirms trend strength", 0.9, "indicator")
        elif state.structure.trend == "BEARISH":
            bear(f"ADX {ind.adx:.0f} confirms trend strength", 0.9, "indicator")
    else:
        bull("Low ADX suggests no strong trend", 0.3, "indicator")
        bear("Low ADX suggests no strong trend", 0.3, "indicator")

    # RSI
    if ind.rsi > 55:
        bull(f"RSI {ind.rsi:.0f} shows bullish momentum", 0.7, "indicator")
    elif ind.rsi < 45:
        bear(f"RSI {ind.rsi:.0f} shows bearish momentum", 0.7, "indicator")
    if ind.rsi > 70:
        bear(f"RSI {ind.rsi:.0f} overbought — reversal risk", 0.8, "indicator")
    elif ind.rsi < 30:
        bull(f"RSI {ind.rsi:.0f} oversold — reversal risk", 0.8, "indicator")

    # VWAP
    if ind.vwap and price > ind.vwap:
        bull("Price above VWAP", 0.6, "indicator")
    elif ind.vwap:
        bear("Price below VWAP", 0.6, "indicator")

    # MSS (recent change of character)
    if state.structure.last_mss is not None:
        if state.structure.last_mss.direction == "bullish":
            bull("Recent bullish MSS (change of character)", 1.3, "structure")
        else:
            bear("Recent bearish MSS (change of character)", 1.3, "structure")

    # session position
    if state.session.session_open is not None and price > state.session.session_open:
        bull("Trading above session open", 0.5, "session")
    elif state.session.session_open is not None:
        bear("Trading below session open", 0.5, "session")

    # liquidity context: price near sell-side below => potential sweep target
    if state.liquidity.nearest_sell_side is not None:
        bear("Sell-side liquidity resting below price", 0.5, "liquidity")
    if state.liquidity.nearest_buy_side is not None:
        bull("Buy-side liquidity resting above price", 0.5, "liquidity")

    return b
