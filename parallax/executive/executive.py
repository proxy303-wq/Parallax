"""The PARALLAX executive — orchestration and permissions.

One evaluation pass:

    perception -> context -> hypotheses -> metacognition -> decision
    -> independent risk gate -> execution -> (reflection on exits)

It never lets a decision reach the broker without a risk authorization, and
it keeps open-position management deterministic even if reasoning is offline.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from parallax.contracts import (
    Action, DecisionClass, EpisodicRecord, ErrorClass, ErrorRecord,
    ExecutionMode, InstrumentId, MarketState, OrderStatus, RiskConfig,
    SystemMode, TradeDecision, new_id,
)
from parallax.adapters.broker import BrokerAdapter, PaperBroker
from parallax.core.brain import Brain, ValidatorEnsemble
from parallax.core.context import build_context
from parallax.core.decision import DecisionConfig, DecisionEngine
from parallax.core.execution import ExecutionBoundary
from parallax.core.hypotheses import HypothesisEngine
from parallax.core.memory import BrainMemory, MemoryStore
from parallax.core.metacognition import MetacognitionEngine
from parallax.core.observability import AuditLog
from parallax.core.perception import build_market_state
from parallax.core.reflection import ReflectionEngine
from parallax.core.risk import RiskContext, RiskEngine
from .modes import ModeManager
from .state_machine import StateMachine


@dataclass
class TickResult:
    decision: TradeDecision
    state: MarketState
    hypothesis_count: int
    executed: bool = False
    order_status: str = ""
    exited: bool = False
    realized_pnl: float = 0.0


@dataclass
class ActiveTrade:
    decision_id: str
    instrument: str
    stop_loss: float | None = None
    target: float | None = None


class ParallaxExecutive:
    def __init__(self, instrument: InstrumentId,
                 risk_config: RiskConfig | None = None,
                 decision_config: DecisionConfig | None = None,
                 mode: ExecutionMode = ExecutionMode.PAPER,
                 broker: BrokerAdapter | None = None,
                 timeframe: str = "5m",
                 lookback: int = 600,
                 brain: Brain | None = None,
                 brain_memory: BrainMemory | None = None,
                 typesafe=None):
        self.instrument = instrument
        self.timeframe = timeframe
        self.lookback = lookback
        self.risk_config = risk_config or RiskConfig()
        self.broker = broker or PaperBroker(
            capital=self.risk_config.capital,
            point_value=self.risk_config.point_value,
            slippage=self.risk_config.slippage,
            fee_rate=self.risk_config.fee_rate)

        self.state_machine = StateMachine(SystemMode.OBSERVING)
        self.modes = ModeManager(mode)

        self.hypotheses = HypothesisEngine()
        self.metacognition = MetacognitionEngine()
        self.decision = DecisionEngine(decision_config, self.metacognition)
        self.risk = RiskEngine(self.risk_config)
        self.execution = ExecutionBoundary(self.broker)
        self.memory = MemoryStore()
        self.reflection = ReflectionEngine()
        self.audit = AuditLog()
        self.brain_memory = brain_memory
        # the brain gate is opt-in: backtests stay deterministic without it
        self.validators = (ValidatorEnsemble(brain=brain, typesafe=typesafe)
                           if (brain is not None or typesafe is not None) else None)

        self.daily_pnl = 0.0
        self.monthly_pnl = 0.0
        self.trades_today = 0
        self.active: ActiveTrade | None = None
        self.last_result: TickResult | None = None
        self.recent_outcomes: list[str] = []

    # ---- core ------------------------------------------------------------
    def evaluate(self, bars, timeframe: str | None = None) -> TickResult:
        tf = timeframe or self.timeframe
        ms = build_market_state(self.instrument, bars, tf, lookback=self.lookback)
        state = build_context({tf: ms}, tf)

        # mark broker price so paper P&L is current
        if state.last_price:
            self.broker.set_price(str(self.instrument), state.last_price)

        hyps = self.hypotheses.generate(state)

        # position management first (deterministic, independent of reasoning)
        positions = self.broker.get_positions()
        if positions and self.active is not None:
            pos = positions[0]
            dec = self.decision.manage(pos, state, self.active.stop_loss,
                                       self.active.target)
            result = TickResult(decision=dec, state=state,
                                hypothesis_count=len(hyps))
            if dec.decision_class == DecisionClass.EXIT:
                pnl = self.broker.close_position(str(self.instrument),
                                                 state.last_price or 0.0)
                result.exited = True
                result.realized_pnl = pnl or 0.0
                self._settle(dec, "WIN" if (pnl or 0) > 0 else "LOSS", pnl or 0.0,
                             state, "position closed")
                self.active = None
                self.state_machine.transition(SystemMode.REFLECTING)
            else:
                self.state_machine.transition(SystemMode.POSITION_ACTIVE)
            self.last_result = result
            return result

        # fresh decision
        dec = self.decision.decide(
            state, hyps,
            recent_outcomes=self.recent_outcomes,
            trades_today=self.trades_today,
            kill_switch=self.modes.kill_switch or self.modes.paused)

        result = TickResult(decision=dec, state=state,
                            hypothesis_count=len(hyps))

        if dec.decision_class == DecisionClass.TRADE:
            # brain gate (advisory/veto-only): DeepSeek may reject or reduce,
            # never force or size a trade the deterministic engine did not propose
            if self.validators is not None:
                vr = self.validators.validate(dec, state, hyps, self.recent_outcomes)
                if vr is not None:
                    if self.brain_memory is not None:
                        self.brain_memory.remember_assessment(vr.assessment.as_dict())
                    if not vr.allowed:
                        self.audit.record(
                            "brain", "VETO", str(self.instrument),
                            {"decision": dec.decision_id,
                             "assessment": vr.assessment.as_dict()},
                            self.modes.mode)
                        self.state_machine.transition(SystemMode.WAITING)
                        self.last_result = result
                        return result
            self.state_machine.transition(SystemMode.SIGNAL_READY)
            ctx = RiskContext(
                equity=self.broker.get_account().equity,
                daily_pnl=self.daily_pnl,
                monthly_pnl=self.monthly_pnl,
                consecutive_losses=self.memory.consecutive_losses(str(self.instrument)),
                open_positions=len(self.broker.get_positions()),
                spread=state.price.spread if state.price else None,
            )
            self.state_machine.transition(SystemMode.RISK_CHECK)
            verdict = self.risk.evaluate(dec, state, ctx)
            if verdict.approved:
                self.state_machine.transition(SystemMode.EXECUTING)
                ack = self.execution.execute(dec, verdict, str(self.instrument),
                                             self.modes.mode)
                result.executed = True
                result.order_status = ack.status.value
                if ack.status == OrderStatus.FILLED:
                    self.active = ActiveTrade(
                        decision_id=dec.decision_id,
                        instrument=str(self.instrument),
                        stop_loss=dec.risk_plan.stop_loss,
                        target=dec.risk_plan.target)
                    self.trades_today += 1
                    self.state_machine.transition(SystemMode.POSITION_ACTIVE)
                    self.audit.record("executive", "ENTRY", str(self.instrument),
                                      {"decision": dec.decision_id,
                                       "qty": verdict.position_size,
                                       "price": dec.risk_plan.entry_price},
                                      self.modes.mode)
            else:
                self.audit.record("risk", "REJECT", str(self.instrument),
                                  {"decision": dec.decision_id,
                                   "reasons": verdict.reasons}, self.modes.mode)
                self.state_machine.transition(SystemMode.WAITING)
        elif dec.decision_class == DecisionClass.ABORT:
            self.state_machine.transition(SystemMode.PAUSED)
        else:
            self.state_machine.transition(SystemMode.WAITING)

        self.last_result = result
        return result

    def _settle(self, decision: TradeDecision, outcome: str, pnl: float,
                state: MarketState, notes: str = "") -> None:
        self.daily_pnl += pnl
        self.monthly_pnl += pnl
        if self.brain_memory is not None:
            self.brain_memory.record_regime(state.regime.regime.value)
            self.brain_memory.record_outcome(outcome, decision.action.value)
        if outcome == "LOSS":
            self.recent_outcomes.append("LOSS")
        else:
            self.recent_outcomes.append("WIN")
        rec = EpisodicRecord(
            id=new_id("ep", decision.timestamp),
            timestamp=decision.timestamp,
            instrument=str(self.instrument),
            decision_id=decision.decision_id,
            action=decision.action.value,
            outcome=outcome,
            pnl=pnl,
            regime=state.regime.regime.value,
            confidence=decision.confidence,
            notes=notes)
        self.memory.record_episodic(rec)
        refl = self.reflection.reflect(decision, outcome, pnl,
                                       state.regime.regime.value)
        if refl.error_classification != ErrorClass.NONE:
            self.memory.record_error(ErrorRecord(
                id=new_id("err", decision.timestamp),
                timestamp=decision.timestamp,
                error_class=refl.error_classification,
                description="; ".join(refl.lessons) or refl.outcome,
                decision_id=decision.decision_id))
        self.audit.record("executive", "EXIT", str(self.instrument),
                          {"decision": decision.decision_id, "pnl": pnl,
                           "outcome": outcome}, self.modes.mode)

    # ---- control ---------------------------------------------------------
    def pause(self) -> None:
        self.modes.pause()
        self.state_machine.transition(SystemMode.PAUSED)
        self.audit.record("executive", "PAUSE", "", {}, self.modes.mode)

    def resume(self) -> None:
        self.modes.resume()
        self.state_machine.transition(SystemMode.OBSERVING)
        self.audit.record("executive", "RESUME", "", {}, self.modes.mode)

    def kill(self) -> None:
        self.modes.kill()
        self.state_machine.force(SystemMode.EMERGENCY_STOP)
        self.audit.record("executive", "KILL", "", {}, self.modes.mode)

    def summary(self) -> dict:
        acct = self.broker.get_account()
        return {
            "mode": self.modes.mode.value,
            "system_state": self.state_machine.state.value,
            "kill_switch": self.modes.kill_switch,
            "paused": self.modes.paused,
            "equity": round(acct.equity, 2),
            "daily_pnl": round(self.daily_pnl, 2),
            "monthly_pnl": round(self.monthly_pnl, 2),
            "trades_today": self.trades_today,
            "open_positions": len(self.broker.get_positions()),
        }
