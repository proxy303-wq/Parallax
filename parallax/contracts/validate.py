"""PARALLAX — boundary validation (§17.1).

Reject malformed messages at the boundary.  These deterministic schema rules
are applied by every adapter and executor before an object crosses a module
boundary.  They are intentionally small and explicit — no reflection, no
schema library — so behaviour is auditable.
"""
from __future__ import annotations

from .enums import OrderType, Side
from .orders import OrderIntent
from .risk import RiskConfig


class ValidationError(ValueError):
    pass


def validate_order_intent(intent: OrderIntent) -> list[str]:
    """Return a list of errors (empty list => valid)."""
    errs: list[str] = []
    if not intent.intent_id:
        errs.append("missing intent_id")
    if not intent.decision_id:
        errs.append("missing decision_id")
    if not intent.risk_auth_id:
        errs.append("missing risk_auth_id")
    if not intent.idempotency_key:
        errs.append("missing idempotency_key")
    if not intent.instrument:
        errs.append("missing instrument")
    if intent.quantity <= 0:
        errs.append("quantity must be > 0")
    if intent.order_type not in (OrderType.MARKET, OrderType.LIMIT,
                                 OrderType.STOP, OrderType.STOP_LIMIT):
        errs.append("invalid order_type")
    if intent.side not in (Side.BUY, Side.SELL):
        errs.append("invalid side")
    if intent.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and (
            intent.price is None or intent.price <= 0):
        errs.append("limit/stop-limit requires a positive price")
    return errs


def validate_risk_config(cfg: RiskConfig) -> list[str]:
    errs: list[str] = []
    if cfg.capital <= 0:
        errs.append("capital must be > 0")
    if cfg.max_risk_per_trade_pct <= 0 or cfg.max_risk_per_trade_pct > 0.1:
        errs.append("max_risk_per_trade_pct out of range (0, 0.1]")
    if cfg.max_daily_loss_pct <= 0:
        errs.append("max_daily_loss_pct must be > 0")
    if cfg.max_consecutive_losses < 1:
        errs.append("max_consecutive_losses must be >= 1")
    return errs
