"""Live deployment config — 3-lot NIFTY futures sized off live Dhan capital.

The RiskEngine sizes risk_amount = broker.equity * max_risk_per_trade_pct, so
with a DhanBroker supplying the live wallet balance, 1% risk + a ~38pt stop
gives exactly 3 lots at Rs7.5L (3 x Rs2,470).  max_position_size=3 is the hard
ceiling so it never exceeds the requested 3 lots as capital grows.
"""
from __future__ import annotations

from parallax.contracts import RiskConfig, spec_for


def dhan_futures_risk_config(capital: float | None = None, max_lots: int = 3,
                             risk_pct: float = 0.01) -> RiskConfig:
    spec = spec_for("NIFTY")
    return RiskConfig(
        capital=capital or spec.default_capital,
        max_risk_per_trade_pct=risk_pct,       # 1% -> 3 lots at Rs7.5L
        max_daily_loss_pct=0.03,
        max_monthly_loss_pct=0.06,
        max_open_positions=1,
        max_position_size=max_lots,            # hard ceiling at 3 lots
        max_consecutive_losses=4,
        point_value=spec.point_value,          # 65
        lot_size=spec.lot_size,                # 65
        min_step=spec.min_step,                # 1 lot
        fee_rate=spec.fee_rate,
        slippage=spec.slippage,
        max_slippage_pct=0.002,
        max_spread_pct=0.001,
        max_cost_ratio=0.20,
        data_staleness_tolerance_seconds=3600.0,
        session_restrictions=["OPEN"],
    )


def lots_from_capital(capital: float, stop_dist: float,
                      risk_pct: float = 0.01, max_lots: int = 3,
                      point_value: float = 65.0) -> int:
    """How many lots the live capital supports at the given stop distance."""
    import math
    risk_amount = capital * risk_pct
    per_lot = stop_dist * point_value
    lots = math.floor(risk_amount / per_lot) if per_lot > 0 else 0
    return max(0, min(max_lots, lots))
