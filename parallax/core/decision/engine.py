"""Decision engine — turns hypotheses + metacognition into a decision class.

Decision classes (§10): WAIT, TRADE, HOLD, EXIT, ABORT.  A TRADE requires a
validated edge; WAIT is the disciplined default; ABORT is raised on any
operational/risk/data-integrity issue.  The decision engine proposes a risk
plan (entry/stop/target/RR) but never finalises sizing — that is the
independent risk gate's job.
"""
from __future__ import annotations

from dataclasses import dataclass

from parallax.contracts import (
    Action, DataQuality, DecisionClass, Direction, Hypothesis, MarketState,
    Position, RegimeClass, RiskPlan, Side, TradeDecision, new_id,
)
from parallax.core.metacognition import MetacognitionEngine
from parallax.core.reasoning import calibrate_confidence, expected_value


@dataclass
class DecisionConfig:
    min_confidence: float = 0.6
    min_ev: float = 0.15            # expected value in units of risk
    min_reward_risk: float = 2.0
    atr_stop_mult: float = 2.0      # stop width in ATR multiples (wider = less cost drag)
    max_trades_per_day: int = 4
    require_confirmation: bool = True
    trend_only: bool = True         # only trade trending regimes (with the trend)
    min_adx: float = 18.0           # hard ADX floor for directional trades


class DecisionEngine:
    def __init__(self, config: DecisionConfig | None = None,
                 meta: MetacognitionEngine | None = None):
        self.config = config or DecisionConfig()
        self.meta = meta or MetacognitionEngine()

    # -- fresh decision ----------------------------------------------------
    def decide(self, state: MarketState, hypotheses: list[Hypothesis],
               recent_outcomes: list[str] | None = None,
               trades_today: int = 0,
               kill_switch: bool = False) -> TradeDecision:
        ts = state.timestamp
        instrument = str(state.instrument)

        # operational / integrity aborts
        if kill_switch:
            return self._abort(state, "kill switch engaged")
        if not state.data_quality.healthy:
            return self._abort(state, f"data quality {state.data_quality.status.value}")

        # pick the best directional hypothesis
        directional = [h for h in hypotheses
                       if h.direction in (Direction.LONG, Direction.SHORT)]
        if not directional:
            return self._wait(state, "no directional hypothesis", hypotheses)

        best = max(directional, key=lambda h: h.confidence)
        side = Side.BUY if best.direction == Direction.LONG else Side.SELL

        # trend / regime gate — trade only with the higher-timeframe trend
        if self.config.min_adx > 0 and state.indicators.adx < self.config.min_adx:
            return self._wait(state, f"ADX {state.indicators.adx:.0f} below trend floor", hypotheses)
        if self.config.trend_only:
            reg = state.regime.regime
            if reg not in (RegimeClass.TRENDING_BULLISH, RegimeClass.TRENDING_BEARISH):
                return self._wait(state, f"regime {reg.value} is not trending", hypotheses)
            if best.direction == Direction.LONG and reg != RegimeClass.TRENDING_BULLISH:
                return self._wait(state, "long signal in a non-bullish regime", hypotheses)
            if best.direction == Direction.SHORT and reg != RegimeClass.TRENDING_BEARISH:
                return self._wait(state, "short signal in a non-bearish regime", hypotheses)

        # confirmation gate
        if self.config.require_confirmation and not all(
                c.met for c in best.required_confirmations):
            return self._wait(state, "confirmation conditions not all met", hypotheses)

        # calibrated confidence
        calibrated = self.meta.calibrated_confidence(best.confidence)
        conf = calibrated if calibrated is not None else best.confidence

        # reward:risk plan
        plan = self._risk_plan(side, state)
        rr = plan.reward_risk if plan.reward_risk is not None else self.config.min_reward_risk

        # bias firewall — blocking flags force WAIT, downgrading flags reduce
        audit = self.meta.audit(best, state, reward_risk=rr,
                                recent_outcomes=recent_outcomes,
                                trades_today=trades_today)
        fired = [f for f in audit.bias_flags if f.triggered]
        blocking = [f for f in fired if f.blocking]
        if blocking:
            return self._wait(
                state, "bias firewall: " + ", ".join(f.name for f in blocking), hypotheses)
        for f in fired:
            if f.name == "overconfidence":
                conf = min(conf, 0.85)
            elif f.name == "confirmation":
                conf *= 0.9

        ev = expected_value(conf, rr)

        if conf < self.config.min_confidence:
            return self._wait(state, f"confidence {conf:.2f} below threshold", hypotheses)
        if rr < self.config.min_reward_risk:
            return self._wait(state, f"reward:risk {rr:.2f} below minimum", hypotheses)
        if ev < self.config.min_ev:
            return self._wait(state, f"expected value {ev:.2f} below threshold", hypotheses)
        if trades_today >= self.config.max_trades_per_day:
            return self._wait(state, "max trades per day reached", hypotheses)

        decision = TradeDecision(
            decision_id=new_id("dec", ts),
            timestamp=ts,
            instrument=instrument,
            action=Action.BUY if side == Side.BUY else Action.SELL,
            decision_class=DecisionClass.TRADE,
            setup_id=best.id,
            thesis=best.thesis,
            evidence_ids=[e.id for e in best.evidence],
            counter_evidence_ids=[e.id for e in best.counter_evidence],
            invalidation=best.invalidation_conditions,
            expected_value=round(ev, 4),
            confidence=round(best.confidence, 4),
            calibrated_confidence=round(conf, 4) if calibrated is not None else None,
            risk_plan=plan,
            reasoning_trace_id=best.id,
            reasons=[f"setup {best.id} confidence {best.confidence:.2f} EV {ev:.2f}"],
        )
        return decision

    # -- position management ----------------------------------------------
    def manage(self, position: Position, state: MarketState,
               stop_loss: float | None, target: float | None) -> TradeDecision:
        ts = state.timestamp
        price = state.last_price or 0.0
        action = Action.HOLD
        cls = DecisionClass.HOLD
        reasons: list[str] = []
        if position.side == Side.BUY:
            hit_stop = stop_loss is not None and price <= stop_loss
            hit_target = target is not None and price >= target
        else:
            hit_stop = stop_loss is not None and price >= stop_loss
            hit_target = target is not None and price <= target
        if hit_stop:
            action, cls = Action.EXIT, DecisionClass.EXIT
            reasons.append("stop loss breached")
        elif hit_target:
            action, cls = Action.EXIT, DecisionClass.EXIT
            reasons.append("target reached")
        else:
            reasons.append("thesis still valid")
        return TradeDecision(
            decision_id=new_id("dec", ts),
            timestamp=ts,
            instrument=position.instrument,
            action=action,
            decision_class=cls,
            thesis="position management",
            risk_plan=RiskPlan(stop_loss=stop_loss, target=target),
            reasons=reasons,
        )

    # -- helpers -----------------------------------------------------------
    def _risk_plan(self, side: Side, state: MarketState) -> RiskPlan:
        """ATR-based stop with a fixed reward:risk target; the structural
        liquidity level is recorded separately as the thesis invalidation."""
        price = state.last_price or 0.0
        atr = state.indicators.atr or 0.0
        if atr > 0:
            stop_dist = atr * self.config.atr_stop_mult
        else:
            stop_dist = price * 0.002  # fallback 0.2% of price
        rr = self.config.min_reward_risk
        if side == Side.BUY:
            stop = price - stop_dist
            target = price + rr * stop_dist
            invalidation = state.liquidity.nearest_sell_side
        else:
            stop = price + stop_dist
            target = price - rr * stop_dist
            invalidation = state.liquidity.nearest_buy_side
        return RiskPlan(entry_price=price, stop_loss=round(stop, 2),
                        target=round(target, 2), reward_risk=round(rr, 3),
                        invalidation_level=round(invalidation, 2) if invalidation else None)

    def _wait(self, state: MarketState, reason: str,
              hypotheses: list[Hypothesis]) -> TradeDecision:
        return TradeDecision(
            decision_id=new_id("dec", state.timestamp),
            timestamp=state.timestamp,
            instrument=str(state.instrument),
            action=Action.WAIT,
            decision_class=DecisionClass.WAIT,
            thesis="no validated edge",
            reasons=[reason],
        )

    def _abort(self, state: MarketState, reason: str) -> TradeDecision:
        return TradeDecision(
            decision_id=new_id("dec", state.timestamp),
            timestamp=state.timestamp,
            instrument=str(state.instrument),
            action=Action.ABORT,
            decision_class=DecisionClass.ABORT,
            thesis="operational/data-integrity abort",
            reasons=[reason],
        )
