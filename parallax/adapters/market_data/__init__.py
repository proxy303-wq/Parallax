"""Market-data adapters — CSV loading, normalization, gateway."""
from .csv_loader import load_ohlcv, validate_bars
from .gateway import MarketDataGateway

__all__ = ["load_ohlcv", "validate_bars", "MarketDataGateway"]
