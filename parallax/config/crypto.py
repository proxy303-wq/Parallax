"""Delta Exchange crypto (BTCUSD perp) - venues, costs and strategy parameters.

Everything here mirrors the walk-forward that produced the numbers in
.research/btc_jas_compare/REPORT.md sections 12-16, so the live worker and the
backtest agree by construction.

Fees are the Delta India schedule, measured from the account's own fills
(0.05% taker x 1.18 GST = 5.90 bp).  The DEMO venue (cdn-ind.testnet.deltaex.org)
publishes the same commission rates as production.
"""
from __future__ import annotations

from dataclasses import dataclass

# ---- venues ---------------------------------------------------------------
DEMO_BASE = "https://cdn-ind.testnet.deltaex.org"
LIVE_BASE = "https://api.india.delta.exchange"

GST = 1.18
MAKER_RATE = 0.0002 * GST      # 2.36 bp
TAKER_RATE = 0.0005 * GST      # 5.90 bp

# Delta BTCUSD (inverse perpetual): 1 contract = 0.001 BTC, tick 0.5
CONTRACT_VALUE = 0.001
TICK_SIZE = 0.5
MAINTENANCE_MARGIN = 0.0025    # 0.25%, from the product spec
MAX_NOTIONAL_USD = 100_000.0   # max_leverage_notional


def base_url(mode: str) -> str:
    """paper and demo both read the DEMO venue; only live hits production."""
    return DEMO_BASE if str(mode).lower() in ("paper", "demo", "test") else LIVE_BASE


@dataclass(frozen=True)
class SMCConfig:
    """Strategy parameters - frozen from the validated walk-forward."""
    symbol: str = "BTCUSD"
    interval: str = "1h"
    swing_length: int = 20          # SMC swing lookback (also the confirmation lag)
    zone: str = "fvg"
    zone_ttl: int = 150             # bars a zone stays valid
    limit_ttl: int = 20             # bars a resting order stays live
    buffer_atr: float = 0.25        # stop sits this far beyond the zone edge
    stop_atr_floor: float = 1.5     # minimum stop distance in ATR (the key refinement)
    trail_atr: float = 5.0          # chandelier trail
    target_R: float | None = None   # None -> let the trail run
    atr_period: int = 14
    risk_pct: float = 0.0075        # 0.75% of equity per trade
    max_leverage: float = 5.0       # 5x (see REPORT.md 15.6)
    allow_short: bool = True


DEFAULT = SMCConfig()
