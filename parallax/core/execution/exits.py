"""Deterministic exit management — the profitable-trader playbook encoded.

The lock/trail mechanism is a lever, but the size of its benefit depends
entirely on where the trade was entered.  The original "31% -> 93% win rate"
measurement came from a backtest that filled on the same bar that set the
impulse extreme: the entry landed at that bar's low, so 0.5R of MFE was
credited instantly and the lock fired on noise-free heat.  With the fill made
causal (see research/strict_combo) the picture inverts - over 2 years of
NIFTY 5m, relaxing lock_r is monotone in both halves of the sample:

    lock 0.5 (default)  PF 1.01  maxDD Rs 62,061
    lock 1.5            PF 1.17  maxDD Rs 58,593
    lock 3.0            PF 1.19  maxDD Rs 48,826
    no lock at all      PF 1.20  maxDD Rs 42,365

Once price moves in favour by lock_r (multiples of the initial risk), the
stop is moved to breakeven (plus an optional floor) and then trailed behind
the peak/trough.  Callers that want no lock should pass lock_r=inf.

Pure functions of price levels — no state beyond the trade, no look-ahead.
"""
from __future__ import annotations

from dataclasses import dataclass

from parallax.contracts import Side


@dataclass
class ExitConfig:
    lock_r: float = 0.5     # MFE (in R) that arms the breakeven stop
    floor_r: float = 0.0    # guaranteed profit (in R) once armed (0 = breakeven)
    trail_r: float = 0.5    # trailing distance (in R) behind the peak/trough
    target_r: float = 2.0   # final target (in R) — the ceiling


class ExitManager:
    """Tracks one position and returns exit decisions on each bar's high/low.

    Conservative convention: if a bar spans both stop and target, the stop is
    assumed to have been hit first.
    """

    def __init__(self, side: Side, entry: float, stop: float, config: ExitConfig,
                 target: float | None = None):
        self.side = side
        self.entry = entry
        self.risk = abs(entry - stop) or 1e-9
        self.config = config
        sign = 1.0 if side == Side.BUY else -1.0
        self.stop = stop
        self.target = target if target is not None else (entry + sign * config.target_r * self.risk)
        self.peak = entry          # best price (high for long, low for short)
        self.armed = False
        self.mfe_r = 0.0           # max favourable excursion, in R
        self.mae_r = 0.0           # max adverse excursion, in R
        self.exit_price: float | None = None
        self.exit_reason: str = ""

    def _sign(self) -> float:
        return 1.0 if self.side == Side.BUY else -1.0

    def update(self, high: float, low: float) -> tuple[float | None, str]:
        """Advance by one bar.  Returns (exit_price, reason) or (None, "").

        Order matters: excursions are tracked first, exits are checked against
        the stop *as of the start of this bar* (stop-first, conservative), and
        only then is the stop armed/trailed for the next bar.
        """
        s = self._sign()
        was_armed = self.armed

        # 1. excursion tracking
        if s > 0:
            self.peak = max(self.peak, high)
            self.mfe_r = max(self.mfe_r, (self.peak - self.entry) / self.risk)
            self.mae_r = max(self.mae_r, (self.entry - low) / self.risk)
        else:
            self.peak = min(self.peak, low)
            self.mfe_r = max(self.mfe_r, (self.entry - self.peak) / self.risk)
            self.mae_r = max(self.mae_r, (high - self.entry) / self.risk)

        # 2. exit checks — stop first (conservative), then target
        if s > 0 and low <= self.stop:
            self.exit_price, self.exit_reason = self.stop, "stop"
            return self.exit_price, self.exit_reason
        if s < 0 and high >= self.stop:
            self.exit_price, self.exit_reason = self.stop, "stop"
            return self.exit_price, self.exit_reason
        if s > 0 and high >= self.target:
            self.exit_price, self.exit_reason = self.target, "target"
            return self.exit_price, self.exit_reason
        if s < 0 and low <= self.target:
            self.exit_price, self.exit_reason = self.target, "target"
            return self.exit_price, self.exit_reason

        # 3. no exit — arm (breakeven/floor) or trail for the NEXT bar
        if not was_armed and self.mfe_r >= self.config.lock_r:
            self.armed = True
            self.stop = self.entry + s * self.config.floor_r * self.risk
        elif was_armed and self.config.trail_r > 0:
            if s > 0:
                self.stop = max(self.stop, self.peak - self.config.trail_r * self.risk)
            else:
                self.stop = min(self.stop, self.peak + self.config.trail_r * self.risk)
        return None, ""

    def force_exit(self, price: float) -> float:
        """Flatten at price (session end / time stop)."""
        self.exit_price = price
        self.exit_reason = "flatten"
        return price

    @property
    def r_multiple(self) -> float:
        """Gross R multiple (before fees/slippage) at the exit price."""
        if self.exit_price is None:
            return 0.0
        return self._sign() * (self.exit_price - self.entry) / self.risk
