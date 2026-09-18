"""PARALLAX — execution & broker boundary contracts (§12)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .enums import ExecutionMode, OrderStatus, OrderType, Side, TimeInForce


@dataclass
class OrderIntent:
    intent_id: str
    decision_id: str
    risk_auth_id: str
    instrument: str
    side: Side
    quantity: float
    order_type: OrderType
    price: Optional[float] = None
    trigger_price: Optional[float] = None
    stop_policy: Optional[dict[str, float]] = None     # {"stop": price}
    target_policy: Optional[dict[str, float]] = None   # {"target": price}
    time_in_force: TimeInForce = TimeInForce.DAY
    idempotency_key: str = ""
    timestamp: Optional[datetime] = None
    mode: ExecutionMode = ExecutionMode.PAPER
    client_tag: str = "PARALLAX"

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id, "decision_id": self.decision_id,
            "risk_auth_id": self.risk_auth_id, "instrument": self.instrument,
            "side": self.side.value, "quantity": self.quantity,
            "order_type": self.order_type.value, "price": self.price,
            "idempotency_key": self.idempotency_key, "mode": self.mode.value,
        }


@dataclass
class ValidatedOrderIntent:
    intent: OrderIntent
    valid: bool
    errors: list[str] = field(default_factory=list)
    risk_authorized: bool = False


@dataclass
class OrderAck:
    order_id: str
    intent_id: str
    status: OrderStatus
    filled_qty: float = 0.0
    avg_price: Optional[float] = None
    message: str = ""


@dataclass
class Order:
    order_id: str
    intent_id: str
    decision_id: str
    instrument: str
    side: Side
    quantity: float
    status: OrderStatus = OrderStatus.NEW
    filled_qty: float = 0.0
    avg_price: Optional[float] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class Position:
    instrument: str
    side: Side
    quantity: float
    avg_entry: float
    current_price: Optional[float] = None
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    opened_at: Optional[datetime] = None
    decision_id: str = ""


@dataclass
class AccountState:
    cash: float
    equity: float = 0.0
    margin_used: float = 0.0
    buying_power: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    currency: str = "INR"


@dataclass
class Quote:
    instrument: str
    timestamp: datetime
    bid: float
    ask: float
    last: float

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass
class BrokerReconciliation:
    ok: bool
    positions_match: bool = True
    orders_match: bool = True
    discrepancies: list[str] = field(default_factory=list)


@dataclass
class ModifyOrderRequest:
    order_id: str
    price: Optional[float] = None
    quantity: Optional[int] = None
    trigger_price: Optional[float] = None


@dataclass
class CancelAck:
    order_id: str
    status: OrderStatus
    message: str = ""


@dataclass
class ModifyAck:
    order_id: str
    status: OrderStatus
    message: str = ""
