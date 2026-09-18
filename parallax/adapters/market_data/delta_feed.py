"""Delta Exchange market-data adapter (India production).

Fetches public candles and tickers (no key needed) and returns PARALLAX Bars.
The India venue serves the inverse perps the account keys belong to
(BTCUSD id 27, XAUTUSD, ...).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from parallax.contracts import Bar

BASE_URL = "https://api.india.delta.exchange"
_RES_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800,
                "1h": 3600, "4h": 14400, "1d": 86400}


class DeltaFeed:
    def __init__(self, base_url: str = BASE_URL, timeout: int = 20):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, query: str | None = None):
        url = self.base_url + path + (("?" + query) if query else "")
        req = urllib.request.Request(url, headers={
            "Accept": "application/json", "User-Agent": "parallax/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Delta {path} -> HTTP {e.code}") from e
        return body.get("result", body) if isinstance(body, dict) else body

    def ticker(self, symbol: str) -> dict:
        raw = self._get(f"/v2/tickers/{symbol}")
        return raw if isinstance(raw, dict) else {}

    def candles(self, symbol: str, resolution: str = "5m",
                limit: int = 300, end: int | None = None) -> list[Bar]:
        step = _RES_SECONDS.get(resolution, 300)
        now = int(time.time())
        end = end or now
        start = end - limit * step
        raw = self._get("/v2/history/candles",
                        f"resolution={resolution}&symbol={symbol}&start={start}&end={end}")
        raw = raw if isinstance(raw, list) else []
        bars: list[Bar] = []
        for c in raw:
            t = c.get("time", 0)
            if not t:
                continue
            bars.append(Bar(
                ts=datetime.fromtimestamp(int(t), tz=timezone.utc),
                open=float(c.get("open", 0.0)),
                high=float(c.get("high", 0.0)),
                low=float(c.get("low", 0.0)),
                close=float(c.get("close", 0.0)),
                volume=float(c.get("volume", 0.0)),
            ))
        bars.sort(key=lambda b: b.ts)
        return bars[-limit:]
