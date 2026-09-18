"""Contract + boundary-validation tests (§17, §27)."""
from datetime import datetime, timezone

import pytest

from parallax.contracts import (
    DecisionClass, OrderIntent, OrderType, RiskConfig, Side, TradeDecision,
    Action, new_id, validate_order_intent, validate_risk_config,
)


def _intent(**kw) -> OrderIntent:
    base = dict(intent_id="i1", decision_id="d1", risk_auth_id="r1",
                instrument="NIFTY", side=Side.BUY, quantity=10,
                order_type=OrderType.MARKET, idempotency_key="k1")
    base.update(kw)
    return OrderIntent(**base)


def test_valid_intent_passes():
    assert validate_order_intent(_intent()) == []


def test_intent_missing_idempotency_key_rejected():
    errs = validate_order_intent(_intent(idempotency_key=""))
    assert any("idempotency" in e for e in errs)


def test_intent_missing_risk_auth_rejected():
    errs = validate_order_intent(_intent(risk_auth_id=""))
    assert any("risk_auth" in e for e in errs)


def test_intent_limit_requires_price():
    errs = validate_order_intent(_intent(order_type=OrderType.LIMIT, price=None))
    assert any("price" in e for e in errs)


def test_intent_negative_qty_rejected():
    errs = validate_order_intent(_intent(quantity=-5))
    assert any("quantity" in e for e in errs)


def test_risk_config_validation():
    assert validate_risk_config(RiskConfig()) == []
    assert validate_risk_config(RiskConfig(capital=0)) != []
    assert validate_risk_config(RiskConfig(max_risk_per_trade_pct=0.5)) != []


def test_ids_are_unique_and_stamped():
    a = new_id("dec")
    b = new_id("dec")
    assert a != b
    assert a.startswith("dec_")


def test_decision_contract_fields():
    d = TradeDecision(decision_id="x", timestamp=datetime.now(timezone.utc),
                      instrument="NIFTY", action=Action.WAIT,
                      decision_class=DecisionClass.WAIT)
    assert not d.is_trade
    assert d.as_dict()["decision_class"] == "WAIT"
