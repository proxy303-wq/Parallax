"""Hypothesis engine — generate competing market hypotheses with confidence.

Produces LONG / SHORT / RANGE / VOL and always a NO-TRADE hypothesis.  Each
hypothesis carries supporting evidence, counter-evidence, required
confirmations, invalidation conditions, a calibrated confidence and status.
"""
from __future__ import annotations

from parallax.contracts import (
    CalibrationMetadata, Condition, ConditionKind, Direction, EvidenceItem,
    EvidenceKind, Hypothesis, HypothesisStatus, InstrumentType, MarketState,
    RegimeClass,
)
from parallax.core.knowledge import KnowledgeBase
from parallax.core.reasoning import gather_evidence, sigmoid, softmax

_CONF_SCALE = 0.35  # logit sensitivity of the directional score


class HypothesisEngine:
    def __init__(self, knowledge: KnowledgeBase | None = None):
        self.kb = knowledge or KnowledgeBase()

    # -- public -----------------------------------------------------------
    def generate(self, state: MarketState) -> list[Hypothesis]:
        ev = gather_evidence(state)
        net = ev.net
        # softmax over long / short / range logits
        p_long, p_short, p_range = softmax([net * _CONF_SCALE,
                                            -net * _CONF_SCALE,
                                            1.5 - abs(net) * _CONF_SCALE])
        vol_pct = state.volatility.vol_percentile
        p_vol = sigmoid((vol_pct - 0.5) * 6.0) if vol_pct is not None else 0.5

        return [
            self._directional("H1_LONG", Direction.LONG, "bullish", ev, p_long, state),
            self._directional("H2_SHORT", Direction.SHORT, "bearish", ev, p_short, state),
            self._range(ev, p_range, state),
            self._vol_expansion(p_vol, state),
            self._no_trade(p_long, p_short, state),
        ]

    # -- builders ---------------------------------------------------------
    def _directional(self, hid: str, direction: Direction, side: str,
                     ev, confidence: float, state: MarketState) -> Hypothesis:
        supporting = ev.bullish if side == "bullish" else ev.bearish
        counter = ev.bearish if side == "bullish" else ev.bullish
        price = state.last_price or 0.0

        if side == "bullish":
            thesis = "Bullish continuation / reversal into buy-side liquidity"
            invalidation = state.liquidity.nearest_sell_side or (
                price - state.indicators.atr * 1.5 if state.indicators.atr else None)
        else:
            thesis = "Bearish continuation / reversal into sell-side liquidity"
            invalidation = state.liquidity.nearest_buy_side or (
                price + state.indicators.atr * 1.5 if state.indicators.atr else None)

        required = [
            Condition(f"{hid}_conf_trend", ConditionKind.CONFIRMATION,
                      "Trend and EMA alignment agree with the direction",
                      met=self._trend_agrees(state, side)),
            Condition(f"{hid}_conf_mss", ConditionKind.CONFIRMATION,
                      "A recent change-of-character or break supports the direction",
                      met=self._mss_supports(state, side)),
        ]
        # session-local liquidity reference (index futures only): the opening
        # range must have been swept first — replacing the PDH/PDL substitute
        # the corpus found to be worse-than-random.
        if state.instrument.kind == InstrumentType.INDEX_FUTURE:
            swept = (state.structure.swept_opening_low if side == "bullish"
                     else state.structure.swept_opening_high)
            required.append(Condition(
                f"{hid}_conf_opening_sweep", ConditionKind.CONFIRMATION,
                "Opening-range liquidity swept before entry",
                met=swept))
        inval = [
            Condition(f"{hid}_inv_level", ConditionKind.INVALIDATION,
                      f"Price trades through invalidation {invalidation:.1f}" if invalidation
                      else "Structure level breaks the other way",
                      met=False),
        ]
        status = self._status(required, inval)
        if status == HypothesisStatus.CONFIRMED:
            confidence = max(confidence, 0.6)

        return Hypothesis(
            id=hid, direction=direction, thesis=thesis,
            prior_estimate=0.5,
            evidence=[EvidenceItem(e.id, EvidenceKind.SUPPORTING, e.description,
                                   e.weight, e.source) for e in supporting],
            counter_evidence=[EvidenceItem(e.id, EvidenceKind.COUNTER, e.description,
                                           e.weight, e.source) for e in counter],
            required_confirmations=required,
            invalidation_conditions=inval,
            confidence=round(confidence, 4),
            confidence_calibration=CalibrationMetadata(bucket=_bucket(confidence)),
            status=status,
        )

    def _range(self, ev, confidence: float, state: MarketState) -> Hypothesis:
        price = state.last_price or 0.0
        hi = state.session.session_high
        lo = state.session.session_low
        required = [
            Condition("H3_conf_flat", ConditionKind.CONFIRMATION,
                      "ADX is low (no trend)",
                      met=state.indicators.adx < 20.0),
            Condition("H3_conf_bounds", ConditionKind.CONFIRMATION,
                      "Price is inside the session range",
                      met=(hi is not None and lo is not None and lo < price < hi)),
        ]
        inval = [
            Condition("H3_inv_break", ConditionKind.INVALIDATION,
                      "Range boundary broken with displacement", met=False),
        ]
        return Hypothesis(
            id="H3_RANGE", direction=Direction.NEUTRAL,
            thesis="Mean reversion inside the current range",
            prior_estimate=0.3,
            evidence=[],
            counter_evidence=[EvidenceItem(e.id, EvidenceKind.COUNTER, e.description,
                                           e.weight, e.source)
                              for e in ev.bullish + ev.bearish][:3],
            required_confirmations=required,
            invalidation_conditions=inval,
            confidence=round(confidence, 4),
            confidence_calibration=CalibrationMetadata(bucket=_bucket(confidence)),
            status=self._status(required, inval),
        )

    def _vol_expansion(self, confidence: float, state: MarketState) -> Hypothesis:
        return Hypothesis(
            id="H4_VOL", direction=Direction.NEUTRAL,
            thesis="Volatility expansion / breakout regime",
            prior_estimate=0.25,
            evidence=[EvidenceItem("H4_vol", EvidenceKind.SUPPORTING,
                                   f"Volatility percentile {state.volatility.vol_percentile}",
                                   1.0, "volatility")],
            counter_evidence=[],
            required_confirmations=[],
            invalidation_conditions=[],
            confidence=round(confidence, 4),
            confidence_calibration=CalibrationMetadata(bucket=_bucket(confidence)),
            status=HypothesisStatus.ACTIVE,
        )

    def _no_trade(self, p_long: float, p_short: float, state: MarketState) -> Hypothesis:
        conf = round(1.0 - max(p_long, p_short), 4)
        return Hypothesis(
            id="H5_NO_TRADE", direction=Direction.NEUTRAL,
            thesis="No validated edge — standing aside is the correct action",
            prior_estimate=0.5,
            evidence=[EvidenceItem("H5_discipline", EvidenceKind.SUPPORTING,
                                   "No-trade is always a first-class option", 1.0, "discipline")],
            counter_evidence=[],
            required_confirmations=[],
            invalidation_conditions=[],
            confidence=conf,
            confidence_calibration=CalibrationMetadata(bucket=_bucket(conf)),
            status=HypothesisStatus.ACTIVE,
        )

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _trend_agrees(state: MarketState, side: str) -> bool:
        trend = state.structure.trend
        ema_up = state.indicators.ema_fast > state.indicators.ema_slow
        if side == "bullish":
            return trend == "BULLISH" and ema_up
        return trend == "BEARISH" and not ema_up

    @staticmethod
    def _mss_supports(state: MarketState, side: str) -> bool:
        want = "bullish" if side == "bullish" else "bearish"
        # a recent break of structure (continuation) OR change of character
        # (reversal) in the direction both confirm the thesis
        for b in (state.structure.last_break, state.structure.last_mss):
            if b is not None and b.direction == want:
                return True
        return False

    @staticmethod
    def _status(required: list[Condition], inval: list[Condition]) -> HypothesisStatus:
        if any(c.met for c in inval):
            return HypothesisStatus.INVALIDATED
        if required and all(c.met for c in required):
            return HypothesisStatus.CONFIRMED
        if required and any(c.met for c in required):
            return HypothesisStatus.ACTIVE
        return HypothesisStatus.ACTIVE


def _bucket(conf: float) -> str:
    lo = int(conf * 10) / 10
    hi = lo + 0.1
    return f"{lo:.1f}-{hi:.1f}"
