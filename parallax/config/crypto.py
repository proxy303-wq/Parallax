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

# The account is INR-margined but BTCUSD is quoted in USD.  Every risk calculation must
# convert the account balance to USD first -- dividing INR risk by a USD stop distance is
# how a Rs 8L account ends up risking Rs 26L per trade.
USD_INR = 88.0

# Delta BTCUSD (inverse perpetual): 1 contract = 0.001 BTC, tick 0.5
# These remain the BTC defaults for anything not yet symbol-aware.
CONTRACT_VALUE = 0.001
TICK_SIZE = 0.5
MAINTENANCE_MARGIN = 0.0025    # 0.25%, from the product spec
MAX_NOTIONAL_USD = 100_000.0   # max_leverage_notional


@dataclass(frozen=True)
class ProductSpec:
    """Per-symbol Delta contract spec (GET /v2/products/<symbol>, read 2026-09-20).

    These are NOT interchangeable.  ETHUSD is a **0.01** contract -- 10x BTCUSD's 0.001 --
    and XAUTUSD is a 0.001 contract with 1bp maker/taker, half the equity margin and half
    the notional cap.  Sizing an ETH book with the BTC constant reports 10x the real
    contract count; the P&L stays accidentally correct because qty x contract_value is
    invariant, which is exactly why that bug would hide until a demo/live order went out
    with ten times the intended size.
    """
    symbol: str
    contract_value: float      # units of the base asset per contract
    tick_size: float
    maintenance_margin: float
    max_notional_usd: float
    maker_rate: float
    taker_rate: float


PRODUCTS: dict[str, ProductSpec] = {
    "BTCUSD": ProductSpec("BTCUSD", 0.001, 0.5, 0.0025, 100_000.0, MAKER_RATE, TAKER_RATE),
    "ETHUSD": ProductSpec("ETHUSD", 0.01, 0.05, 0.0025, 100_000.0, MAKER_RATE, TAKER_RATE),
    "XAUTUSD": ProductSpec("XAUTUSD", 0.001, 0.01, 0.005, 50_000.0, 0.0001, 0.0001),
}


def product(symbol: str) -> ProductSpec:
    """Contract spec for a symbol; unknown symbols fall back to the validated BTC default."""
    return PRODUCTS.get(str(symbol).upper(), PRODUCTS["BTCUSD"])


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
