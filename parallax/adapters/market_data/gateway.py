"""Market-data gateway — normalized bar access for instruments/timeframes."""
from __future__ import annotations

from parallax.contracts import Bar

from .csv_loader import load_ohlcv


class MarketDataGateway:
    def __init__(self):
        self._bars: dict[tuple[str, str], list[Bar]] = {}

    def load_csv(self, instrument: str, timeframe: str, path: str,
                 tz=None, time_fmt: str | None = None) -> list[Bar]:
        bars = load_ohlcv(path, timeframe, tz=tz, time_fmt=time_fmt)
        self._bars[(instrument, timeframe)] = bars
        return bars

    def set_bars(self, instrument: str, timeframe: str, bars: list[Bar]) -> None:
        self._bars[(instrument, timeframe)] = bars

    def bars(self, instrument: str, timeframe: str) -> list[Bar]:
        return self._bars.get((instrument, timeframe), [])

    def latest(self, instrument: str, timeframe: str, n: int = 600) -> list[Bar]:
        bars = self.bars(instrument, timeframe)
        return bars[-n:] if bars else []
