"""PaperBroker — an in-memory broker for paper / shadow / backtest execution.

Delta-1 futures/perp accounting: P&L is (price move) x quantity x point_value.
Supports idempotency-key de-duplication and position reconciliation.
"""
from __future__ import annotations

from datetime import datetime, timezone

from parallax.contracts import (
    AccountState, BrokerReconciliation, CancelAck, ModifyAck,
    ModifyOrderRequest, Order, OrderAck, OrderStatus, Position, Quote, Side,
    ValidatedOrderIntent, new_id,
)


class PaperBroker:
    def __init__(self, capital: float = 500_000.0, point_value: float = 50.0,
                 currency: str = "INR", slippage: float = 0.0,
                 fee_rate: float = 0.0):
        self.point_value = point_value
        self.currency = currency
        self.slippage = slippage
        self.fee_rate = fee_rate
        self._cash = float(capital)
        self._positions: list[Position] = []
        self._orders: list[Order] = []
        self._seen_keys: set[str] = set()
        self._last_price: dict[str, float] = {}
        self._realized_pnl = 0.0

    # ---- quotes / mark ---------------------------------------------------
    def set_price(self, instrument: str, price: float) -> None:
        self._last_price[instrument] = price
        self._mark(instrument, price)

    def get_quote(self, instrument: str) -> Quote:
        p = self._last_price.get(instrument, 0.0)
        now = datetime.now(timezone.utc)
        return Quote(instrument, now, p, p, p)

    def _mark(self, instrument: str, price: float) -> None:
        for pos in self._positions:
            if pos.instrument == instrument:
                pos.current_price = price
                sign = 1.0 if pos.side == Side.BUY else -1.0
                pos.unrealized_pnl = (price - pos.avg_entry) * sign * pos.quantity * self.point_value

    # ---- account ---------------------------------------------------------
    def get_account(self) -> AccountState:
        unrealized = sum(p.unrealized_pnl for p in self._positions)
        equity = self._cash + unrealized + self._realized_pnl
        return AccountState(
            cash=self._cash, equity=equity, margin_used=0.0,
            buying_power=equity, unrealized_pnl=unrealized,
            realized_pnl=self._realized_pnl, currency=self.currency)

    def get_positions(self) -> list[Position]:
        return list(self._positions)

    def get_open_orders(self) -> list[Order]:
        return [o for o in self._orders if o.status in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED)]

    # ---- execution -------------------------------------------------------
    def place_order(self, intent: ValidatedOrderIntent) -> OrderAck:
        if not intent.valid:
            return OrderAck("", intent.intent.intent_id, OrderStatus.REJECTED,
                            message="; ".join(intent.errors))
        i = intent.intent
        if i.idempotency_key in self._seen_keys:
            return OrderAck("", i.intent_id, OrderStatus.REJECTED,
                            message="duplicate idempotency key")
        self._seen_keys.add(i.idempotency_key)

        price = i.price if i.price else self._last_price.get(i.instrument, 0.0)
        if price <= 0:
            return OrderAck("", i.intent_id, OrderStatus.REJECTED, message="no reference price")

        # apply slippage in the direction of the trade
        slip = self.slippage * price
        fill = price + slip if i.side == Side.BUY else price - slip

        order = Order(order_id=new_id("order"), intent_id=i.intent_id,
                      decision_id=i.decision_id, instrument=i.instrument,
                      side=i.side, quantity=i.quantity, status=OrderStatus.FILLED,
                      filled_qty=i.quantity, avg_price=fill,
                      created_at=datetime.now(timezone.utc),
                      updated_at=datetime.now(timezone.utc))
        self._orders.append(order)

        # update position (delta-1)
        pos = self._find_position(i.instrument, i.side)
        if pos is None:
            pos = Position(instrument=i.instrument, side=i.side, quantity=i.quantity,
                           avg_entry=fill, current_price=fill, opened_at=order.created_at,
                           decision_id=i.decision_id)
            self._positions.append(pos)
        else:
            total_qty = pos.quantity + i.quantity
            pos.avg_entry = (pos.avg_entry * pos.quantity + fill * i.quantity) / total_qty
            pos.quantity = total_qty
            pos.current_price = fill

        # fee (cash debit) — net against realized at close; approximate here
        fee = fill * i.quantity * self.point_value * self.fee_rate
        self._cash -= fee

        self._mark(i.instrument, fill)
        return OrderAck(order.order_id, i.intent_id, OrderStatus.FILLED,
                        filled_qty=i.quantity, avg_price=fill, message="filled")

    def cancel_order(self, order_id: str) -> CancelAck:
        return CancelAck(order_id, OrderStatus.CANCELLED, "no resting orders in paper mode")

    def modify_order(self, request: ModifyOrderRequest) -> ModifyAck:
        return ModifyAck(request.order_id, OrderStatus.REJECTED, "modify not supported in paper mode")

    def reconcile(self) -> BrokerReconciliation:
        return BrokerReconciliation(ok=True, positions_match=True,
                                    orders_match=True, discrepancies=[])

    # ---- position close (used by backtest/exit management) ---------------
    def close_position(self, instrument: str, price: float) -> float | None:
        """Close any position in 'instrument' at price; return realized P&L."""
        pos = self._find_any_position(instrument)
        if pos is None:
            return None
        sign = 1.0 if pos.side == Side.BUY else -1.0
        pnl = (price - pos.avg_entry) * sign * pos.quantity * self.point_value
        fee = price * pos.quantity * self.point_value * self.fee_rate
        self._realized_pnl += pnl - fee
        self._positions.remove(pos)
        self._mark(instrument, price)
        return pnl - fee

    # ---- helpers ---------------------------------------------------------
    def _find_position(self, instrument: str, side: Side) -> Position | None:
        for p in self._positions:
            if p.instrument == instrument and p.side == side:
                return p
        return None

    def _find_any_position(self, instrument: str) -> Position | None:
        for p in self._positions:
            if p.instrument == instrument:
                return p
        return None
