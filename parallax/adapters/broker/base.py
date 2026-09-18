"""Typed broker boundary (§12).

All broker integrations sit behind this interface; the reasoning layer never
touches broker-specific semantics.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from parallax.contracts import (
    AccountState, BrokerReconciliation, CancelAck, ModifyAck,
    ModifyOrderRequest, Order, OrderAck, Position, Quote, ValidatedOrderIntent,
)


class BrokerAdapter(ABC):
    @abstractmethod
    def get_account(self) -> AccountState: ...

    @abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abstractmethod
    def get_open_orders(self) -> list[Order]: ...

    @abstractmethod
    def get_quote(self, instrument: str) -> Quote: ...

    @abstractmethod
    def place_order(self, intent: ValidatedOrderIntent) -> OrderAck: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> CancelAck: ...

    @abstractmethod
    def modify_order(self, request: ModifyOrderRequest) -> ModifyAck: ...

    @abstractmethod
    def reconcile(self) -> BrokerReconciliation: ...
