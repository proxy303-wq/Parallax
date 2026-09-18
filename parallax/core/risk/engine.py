"""Independent risk engine — the hard gate.

Deterministic and separately configurable.  The reasoning system cannot
override it: it validates every trade decision against exposure, loss,
liquidity, data-quality and circuit-breaker rules, then sizes the position
and issues an authorization ID.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from parallax.contracts import (
    Action, MarketState, RiskConfig, RiskStatus, RiskVerdict, TradeDecision,
    new_id,
)


@dataclass
class RiskContext:
    equity: float
    daily_pnl: float = 0.0
    monthly_pnl: float = 0.0
    consecutive_losses: int = 0
    open_positions: int = 0
    correlated_exposure: float = 0.0
    spread: float | None = None
    now_utc: object = None


class RiskEngine:
    def __init__(self, config: RiskConfig | None = None):
        self.config = config or RiskConfig()

    def evaluate(self, decision: TradeDecision, state: MarketState,
                 context: RiskContext) -> RiskVerdict:
        cfg = self.config
        reasons: list[str] = []

        if cfg.kill_switch:
            return self._reject(reasons + ["kill switch engaged"])

        if decision.decision_class.value not in ("TRADE",):
            return self._reject(reasons + [f"decision class {decision.decision_class.value} is not TRADE"])

        # data quality
        if not state.data_quality.healthy:
            reasons.append(f"data quality {state.data_quality.status.value}")
        if state.data_quality.staleness_seconds is not None and                 state.data_quality.staleness_seconds > cfg.data_staleness_tolerance_seconds:
            reasons.append("market data stale beyond tolerance")

        # session restrictions
        if cfg.session_restrictions and state.session.name not in cfg.session_restrictions:
            reasons.append(f"session {state.session.name} not in allowed list")

        # exposure / loss limits
        if context.open_positions >= cfg.max_open_positions:
            reasons.append("max open positions reached")
        if context.daily_pnl <= -cfg.max_daily_loss:
            reasons.append("daily loss limit reached")
        if context.monthly_pnl <= -cfg.max_monthly_loss:
            reasons.append("monthly loss limit reached")
        if context.consecutive_losses >= cfg.max_consecutive_losses:
            reasons.append("consecutive-loss circuit breaker")
        if context.correlated_exposure >= cfg.max_correlated_exposure_pct * context.equity:
            reasons.append("correlated exposure limit reached")

        # spread / slippage
        if context.spread is not None and state.last_price:
            spread_pct = context.spread / state.last_price
            if spread_pct > cfg.max_spread_pct:
                reasons.append(f"spread {spread_pct:.5f} exceeds tolerance")

        # sizing — fractional (coins) or whole (lots) via min_step
        risk_amount = context.equity * cfg.max_risk_per_trade_pct
        stop = decision.risk_plan.stop_loss
        entry = decision.risk_plan.entry_price or state.last_price
        position_size = 0.0
        stop_dist = 0.0
        if stop is not None and entry is not None:
            stop_dist = abs(entry - stop)
            if stop_dist <= 0:
                reasons.append("stop distance is zero")
            else:
                per_unit_risk = stop_dist * cfg.point_value
                step = max(cfg.min_step, 1e-9)
                position_size = math.floor(risk_amount / per_unit_risk / step) * step
        else:
            reasons.append("no stop defined")

        if position_size < cfg.min_step:
            reasons.append("risk budget too small for the minimum size")
        if position_size > cfg.max_position_size:
            position_size = cfg.max_position_size
            reasons.append("position capped at max_position_size")

        # cost-to-risk gate: the stop must be wide enough that round-trip
        # friction stays a small fraction of the risk (independent of size)
        if entry is not None and stop_dist > 0:
            stop_pct = stop_dist / entry
            round_trip_cost_pct = 2.0 * (cfg.fee_rate + cfg.slippage)
            if stop_pct > 0 and round_trip_cost_pct / stop_pct > cfg.max_cost_ratio:
                reasons.append(
                    f"stop {stop_pct:.4%} too tight vs round-trip cost "
                    f"{round_trip_cost_pct:.4%} (widen the stop)")

        if reasons:
            return self._reject(reasons)

        auth_id = new_id("risk")
        return RiskVerdict(
            status=RiskStatus.APPROVED,
            reasons=[],
            risk_amount=round(risk_amount, 2),
            position_size=position_size,
            authorization_id=auth_id,
        )

    def _reject(self, reasons: list[str]) -> RiskVerdict:
        return RiskVerdict(status=RiskStatus.REJECTED, reasons=reasons)
