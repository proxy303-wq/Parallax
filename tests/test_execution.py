"""Execution boundary + broker + reconciliation tests."""
import pytest

from parallax.contracts import (
    Action, DecisionClass, ExecutionMode, OrderStatus, OrderType, RiskStatus,
    RiskVerdict, Side, TradeDecision, ValidationError, new_id,
)
from parallax.adapters.broker import PaperBroker
from parallax.core.execution import ExecutionBoundary, reconcile_positions
from parallax.core.decision import DecisionEngine
from parallax.core.hypotheses import HypothesisEngine

from conftest import make_bars, make_state


def _approved_decision():
    state = make_state(bars=make_bars(seed=3, drift=2.5))
    hyps = HypothesisEngine().generate(state)
    return DecisionEngine().decide(state, hyps)


def _verdict(approved=True):
    return RiskVerdict(status=RiskStatus.APPROVED if approved else RiskStatus.REJECTED,
                       risk_amount=2500.0, position_size=3,
                       authorization_id=new_id("risk"))


def test_broker_fill_creates_position():
    broker = PaperBroker(capital=500000.0, point_value=50.0)
    broker.set_price("NIFTY", 25000.0)
    boundary = ExecutionBoundary(broker)
    dec = _approved_decision()
    ack = boundary.execute(dec, _verdict(), "NIFTY", ExecutionMode.PAPER)
    assert ack.status == OrderStatus.FILLED
    assert len(broker.get_positions()) == 1


def test_idempotency_blocks_duplicate():
    broker = PaperBroker(capital=500000.0, point_value=50.0)
    broker.set_price("NIFTY", 25000.0)
    boundary = ExecutionBoundary(broker)
    dec = _approved_decision()
    boundary.execute(dec, _verdict(), "NIFTY", ExecutionMode.PAPER)
    with pytest.raises(ValidationError):
        boundary.execute(dec, _verdict(), "NIFTY", ExecutionMode.PAPER)


def test_execute_rejects_unapproved():
    broker = PaperBroker(capital=500000.0, point_value=50.0)
    boundary = ExecutionBoundary(broker)
    dec = _approved_decision()
    ack = boundary.execute(dec, _verdict(approved=False), "NIFTY", ExecutionMode.PAPER)
    assert ack.status == OrderStatus.REJECTED


def test_paper_pnl_delta_one():
    broker = PaperBroker(capital=500000.0, point_value=50.0)
    broker.set_price("NIFTY", 25000.0)
    pos = broker._positions  # noqa
    from parallax.contracts import Position
    broker._positions.append(Position("NIFTY", Side.BUY, 2, 25000.0, 25000.0))
    broker.set_price("NIFTY", 25010.0)  # +10 points x 2 x 50 = +1000
    assert broker.get_positions()[0].unrealized_pnl == 1000.0


def test_reconciliation_detects_mismatch():
    from parallax.contracts import Position
    internal = [Position("NIFTY", Side.BUY, 2, 25000.0)]
    broker = [Position("NIFTY", Side.BUY, 3, 25000.0)]
    rec = reconcile_positions(internal, broker)
    assert not rec.ok
    assert any("NIFTY" in d for d in rec.discrepancies)
