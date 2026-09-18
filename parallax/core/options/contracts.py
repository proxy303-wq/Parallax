"""Options contracts — dataclass contracts for the index-options module.

Represents a Dhan index-option contract, a single leg, and a multi-leg
strategy (strangle/spread for selling, directional single-leg for buying).
Premium is in index points; P&L is premium x lot_size x quantity.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class OptionContract:
    symbol: str              # "NIFTY" | "FINNIFTY"
    strike: float            # index points
    expiry: str              # "YYYY-MM-DD"
    option_type: str         # "CE" | "PE"
    lot_size: int            # units per lot
    security_id: int         # Dhan security id
    trading_symbol: str      # Dhan trading symbol
    strike_step: float = 50.0

    def flag(self) -> str:
        return "c" if self.option_type.upper() == "CE" else "p"


@dataclass
class OptionLeg:
    contract: OptionContract
    side: int                # +1 long, -1 short
    quantity: int            # lots (positive int)
    premium: float = 0.0     # index points (entry fill)

    def signed_qty(self) -> int:
        return self.side * self.quantity

    def premium_value(self) -> float:
        """Premium in INR = points x lot_size x quantity."""
        return self.premium * self.contract.lot_size * self.quantity

    def signed_premium_value(self) -> float:
        """Credit (+) for shorts, debit (-) for longs, in INR."""
        return -self.side * self.premium * self.contract.lot_size * self.quantity


@dataclass
class OptionStrategy:
    name: str
    legs: list[OptionLeg] = field(default_factory=list)
    reason: str = ""
    # aggregate Greeks / risk (filled by the engine)
    net_premium: float = 0.0            # INR credit (+) / debit (-)
    max_profit: float = 0.0             # INR
    max_loss: float = 0.0               # INR (positive = risk)
    net_delta: float = 0.0
    net_theta_day: float = 0.0
    net_vega: float = 0.0
    margin_required: float = 0.0        # INR (from Dhan margin calculator)
    prob_profit: float = 0.0            # 0..1 estimate
    break_even: tuple[float, ...] = ()

    def credit(self) -> float:
        return self.net_premium

    def risk_reward(self) -> float:
        """credit / max_loss; None when max_loss <= 0."""
        if self.max_loss <= 0:
            return float("inf")
        return self.net_premium / self.max_loss if self.net_premium > 0 else 0.0

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "reason": self.reason,
            "legs": [f"{l.contract.trading_symbol or str(l.contract.strike)+l.contract.option_type} {l.side:+d}x{l.quantity}"
                     for l in self.legs],
            "net_premium": round(self.net_premium, 2),
            "max_profit": round(self.max_profit, 2),
            "max_loss": round(self.max_loss, 2),
            "net_delta": round(self.net_delta, 3),
            "net_theta_day": round(self.net_theta_day, 2),
            "net_vega": round(self.net_vega, 2),
            "margin_required": round(self.margin_required, 2),
            "prob_profit": round(self.prob_profit, 3),
            "break_even": [round(b, 1) for b in self.break_even],
            "risk_reward": round(self.risk_reward(), 3),
        }


@dataclass
class OptionsConfig:
    # universal
    max_risk_per_trade_pct: float = 0.01     # 1% of equity
    max_premium_debit_pct: float = 0.02      # 2% of equity for option buys
    # selling (short premium)
    sell_delta: float = 0.16                 # short leg |delta| target
    sell_dte_min: int = 3
    sell_dte_max: int = 21
    sell_min_credit_pct: float = 0.25        # min credit / spread width
    sell_prob_profit_min: float = 0.65       # P(in band) floor
    sell_use_spreads: bool = True            # defined risk via long hedge
    spread_width_steps: int = 3              # long hedge N strikes away
    # buying (long premium)
    buy_delta: float = 0.40                  # long leg |delta| target
    buy_dte_min: int = 3
    buy_dte_max: int = 10
    buy_min_prob_profit: float = 0.35
    # vol / regime
    iv_percentile_low: float = 0.25          # sell only below this IV rank
    iv_percentile_high: float = 0.75         # buy only above this IV rank
    # execution
    lot_size: int = 0                        # 0 = resolve from chain
    point_value: float = 1.0                 # premium points x lot -> INR
    min_step: int = 1
