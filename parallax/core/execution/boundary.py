"""Execution boundary — typed order-intent flow.

Decision -> OrderIntent -> schema validation -> risk authorization -> broker.
Enforces idempotency and never lets a conversational message become a broker
command without a validated decision + risk authorization (§12, §30).
"""
from __future__ import annotations

from parallax.contracts import (
    Action, BrokerReconciliation, ExecutionMode, OrderAck, OrderIntent,
    OrderStatus, OrderType, RiskVerdict, Side, TimeInForce, TradeDecision,
    ValidatedOrderIntent, ValidationError, new_id, validate_order_intent,
)
from parallax.adapters.broker import BrokerAdapter


class ExecutionBoundary:
    def __init__(self, broker: BrokerAdapter):
        self.broker = broker
        self._seen_keys: set[str] = set()

    def build_intent(self, decision: TradeDecision, verdict: RiskVerdict,
                     instrument: str, mode: ExecutionMode,
                     order_type: OrderType = OrderType.MARKET) -> OrderIntent:
        side = Side.BUY if decision.action == Action.BUY else Side.SELL
        return OrderIntent(
            intent_id=new_id("ord", decision.timestamp),
            decision_id=decision.decision_id,
            risk_auth_id=verdict.authorization_id,
            instrument=instrument,
            side=side,
            quantity=verdict.position_size,
            order_type=order_type,
            stop_policy={"stop": decision.risk_plan.stop_loss} if decision.risk_plan.stop_loss else None,
            target_policy={"target": decision.risk_plan.target} if decision.risk_plan.target else None,
            time_in_force=TimeInForce.DAY,
            idempotency_key=f"ord_{decision.decision_id}",
            timestamp=decision.timestamp,
            mode=mode,
        )

    def execute(self, decision: TradeDecision, verdict: RiskVerdict,
                instrument: str, mode: ExecutionMode,
                order_type: OrderType = OrderType.MARKET) -> OrderAck:
        if not verdict.approved:
            return OrderAck("", "", OrderStatus.REJECTED, message="risk not approved")
        intent = self.build_intent(decision, verdict, instrument, mode, order_type)

        errs = validate_order_intent(intent)
        if errs:
            raise ValidationError("; ".join(errs))
        if intent.idempotency_key in self._seen_keys:
            raise ValidationError("duplicate idempotency key")

        validated = ValidatedOrderIntent(intent=intent, valid=True, errors=[],
                                         risk_authorized=True)
        ack = self.broker.place_order(validated)
        self._seen_keys.add(intent.idempotency_key)
        return ack

    def reconcile(self) -> BrokerReconciliation:
        return self.broker.reconcile()
