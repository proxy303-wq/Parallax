"""ICT/SMC contracts — configuration and the signal."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ICTConfig:
    # sweep: price must exceed the level by >= sweep_atr * ATR and reclaim within reclaim_bars
    sweep_lookback: int = 30
    sweep_atr: float = 0.10
    reclaim_bars: int = 4
    # displacement: body/range in the top percentile + close in the top/bottom of the candle
    displacement_percentile: float = 0.7
    close_location: float = 0.65
    # FVG: gap size must be >= fvg_min_atr * ATR, created during displacement
    fvg_min_atr: float = 0.10
    # retracement: within how many bars after the FVG may price enter it
    retrace_lookback: int = 10
    # OTE: 62%–79% of the displacement impulse; entry at the 70.5% midpoint
    ote_low: float = 0.62
    ote_high: float = 0.79
    # the draw-on-liquidity objective must clear this reward:risk
    min_rr: float = 1.5


@dataclass
class ICTSignal:
    direction: str               # long | short
    bias: str                    # HTF bias (bullish | bearish)
    sweep_level: float
    sweep_extreme: float         # the swept low (long) / high (short)
    fvg_top: float
    fvg_bottom: float
    entry: float                 # CE midpoint (or OTE)
    stop: float                  # below/above the sweep
    target: float                # DOL — the draw on liquidity
    reasons: list = field(default_factory=list)

    @property
    def reward_risk(self) -> float:
        risk = abs(self.entry - self.stop)
        reward = abs(self.target - self.entry)
        return round(reward / risk, 2) if risk > 0 else 0.0

    def as_dict(self) -> dict:
        return {
            "direction": self.direction, "bias": self.bias,
            "sweep_level": round(self.sweep_level, 1),
            "sweep_extreme": round(self.sweep_extreme, 1),
            "poi": [round(self.fvg_bottom, 1), round(self.fvg_top, 1)],
            "entry": round(self.entry, 1), "stop": round(self.stop, 1),
            "target": round(self.target, 1), "rr": self.reward_risk,
            "reasons": self.reasons,
        }
