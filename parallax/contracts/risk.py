"""PARALLAX — risk contracts (§11)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .enums import RiskStatus


@dataclass
class RiskConfig:
    # capital & exposure
    capital: float = 500_000.0
    max_risk_per_trade_pct: float = 0.005        # 0.5% of equity
    max_daily_loss_pct: float = 0.01
    max_monthly_loss_pct: float = 0.05
    max_open_positions: int = 1
    max_position_size: int = 51
    max_correlated_exposure_pct: float = 0.10
    # loss circuit breaker
    max_consecutive_losses: int = 3
    # execution quality
    max_slippage_pct: float = 0.002
    max_spread_pct: float = 0.001
    min_liquidity: float = 0.0
    # cost-awareness: reject a trade when round-trip friction is too large
    # relative to the per-trade risk budget (the corpus's "cost-to-vol" defect)
    fee_rate: float = 0.0001
    slippage: float = 0.000002     # ~1 tick on NIFTY index futures
    max_cost_ratio: float = 0.20
    # instrument sizing (index futures / perps: value per 1 point per unit)
    point_value: float = 50.0
    lot_size: int = 50
    min_step: float = 1.0      # minimum tradable quantity increment (lots / coins)
    # data / connectivity
    data_staleness_tolerance_seconds: float = 120.0
    # sessions
    session_restrictions: list[str] = field(default_factory=list)
    # hard gates
    kill_switch: bool = False
    require_broker_reconciliation: bool = True

    @property
    def max_risk_per_trade(self) -> float:
        return self.capital * self.max_risk_per_trade_pct

    @property
    def max_daily_loss(self) -> float:
        return self.capital * self.max_daily_loss_pct

    @property
    def max_monthly_loss(self) -> float:
        return self.capital * self.max_monthly_loss_pct


@dataclass
class RiskVerdict:
    status: RiskStatus
    reasons: list[str] = field(default_factory=list)
    risk_amount: float = 0.0
    position_size: float = 0.0
    authorization_id: str = ""

    @property
    def approved(self) -> bool:
        return self.status == RiskStatus.APPROVED

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value, "reasons": self.reasons,
            "risk_amount": self.risk_amount, "position_size": self.position_size,
            "authorization_id": self.authorization_id,
        }
