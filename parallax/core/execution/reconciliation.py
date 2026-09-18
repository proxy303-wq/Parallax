"""Broker-state reconciliation (§11, §12).

Compares internal positions against authoritative broker state.  Any mismatch
is a discrepancy that should degrade the system to a safe state.
"""
from __future__ import annotations

from parallax.contracts import BrokerReconciliation, Position


def reconcile_positions(internal: list[Position],
                        broker: list[Position]) -> BrokerReconciliation:
    discrepancies: list[str] = []
    by_instr = {p.instrument: p for p in broker}
    for p in internal:
        b = by_instr.get(p.instrument)
        if b is None:
            if p.quantity != 0:
                discrepancies.append(f"internal position {p.instrument} missing at broker")
            continue
        if b.side != p.side or b.quantity != p.quantity:
            discrepancies.append(
                f"{p.instrument}: internal {p.side.value}x{p.quantity} vs broker "
                f"{b.side.value}x{b.quantity}")
    for instr in by_instr:
        if not any(p.instrument == instr for p in internal):
            discrepancies.append(f"broker position {instr} missing internally")

    return BrokerReconciliation(
        ok=not discrepancies,
        positions_match=not discrepancies,
        orders_match=True,
        discrepancies=discrepancies,
    )
