"""Dhan live market feed (WebSocket) - real-time ticks for subscribed instruments.

Endpoint:  wss://api-feed.dhan.co?version=2&token=<JWT>&clientId=<id>&authType=2
Subscribe: JSON {"RequestCode": 17, "InstrumentCount": n, "InstrumentList":[...]}
Responses: BINARY, little-endian.  Header is 8 bytes:
    [0] response code (u8)  [1:3] msg length (i16)  [3] exchange segment (u8)
    [4:8] security id (i32)
Codes: 1 index, 2 ticker, 4 quote, 5 OI, 6 prev close, 7 market status,
       8 full (depth), 50 disconnect.
Runs in a daemon thread with its own asyncio loop and keeps a price cache.
"""
from __future__ import annotations

import asyncio
import json
import struct
import threading
import time

WS_URL = ("wss://api-feed.dhan.co?version=2&token={token}"
          "&clientId={cid}&authType=2")

# Dhan feed request codes
REQ_TICKER, REQ_QUOTE, REQ_FULL = 15, 17, 21


class DhanMarketFeed:
    def __init__(self, token: str, client_id: str, instruments: list,
                 mode: int = REQ_QUOTE, on_tick=None):
        self.token = token
        self.client_id = client_id
        self.instruments = [(str(seg), str(sid)) for seg, sid in instruments]
        self.mode = mode
        self.on_tick = on_tick
        self.prices: dict[int, dict] = {}
        self.status = "idle"
        self.ticks = 0
        self._thread = None
        self._stop = False

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True

    @property
    def connected(self) -> bool:
        return self.status == "connected"

    def _run(self) -> None:
        try:
            asyncio.run(self._loop())
        except Exception as e:
            self.status = "error: " + str(e)[:100]

    async def _loop(self) -> None:
        import websockets
        url = WS_URL.format(token=self.token, cid=self.client_id)
        while not self._stop:
            try:
                self.status = "connecting"
                async with websockets.connect(url, ping_interval=10,
                                              ping_timeout=30,
                                              max_size=None) as ws:
                    self.status = "connected"
                    for i in range(0, len(self.instruments), 100):
                        batch = self.instruments[i:i + 100]
                        await ws.send(json.dumps({
                            "RequestCode": self.mode,
                            "InstrumentCount": len(batch),
                            "InstrumentList": [{"ExchangeSegment": s,
                                                "SecurityId": sid}
                                               for s, sid in batch]}))
                    async for raw in ws:
                        if self._stop:
                            break
                        if isinstance(raw, (bytes, bytearray)):
                            self._parse(raw)
                        else:
                            self.status = "msg: " + str(raw)[:60]
            except Exception as e:
                self.status = "reconnect: " + str(e)[:80]
                await asyncio.sleep(3)

    # ---- parsing ---------------------------------------------------------
    def _parse(self, data) -> None:
        if len(data) < 8:
            return
        code = data[0]
        if code == 50:
            self.status = "disconnected by feed"
            return
        try:
            sid = struct.unpack_from("<i", data, 4)[0]
        except struct.error:
            return
        rec = None
        if code == 1 and len(data) >= 32:            # index
            v = struct.unpack_from("<f", data, 8)[0]
            rec = {"ltp": float(v), "kind": "index"}
        elif code == 2 and len(data) >= 16:          # ticker
            v = struct.unpack_from("<f", data, 8)[0]
            rec = {"ltp": float(v), "kind": "ticker"}
        elif code == 4 and len(data) >= 50:          # quote
            ltp = struct.unpack_from("<f", data, 8)[0]
            vol = struct.unpack_from("<i", data, 22)[0]
            o = struct.unpack_from("<f", data, 34)[0]
            c = struct.unpack_from("<f", data, 38)[0]
            h = struct.unpack_from("<f", data, 42)[0]
            l = struct.unpack_from("<f", data, 46)[0]
            rec = {"ltp": float(ltp), "volume": int(vol), "open": float(o),
                   "close": float(c), "high": float(h), "low": float(l),
                   "kind": "quote"}
        elif code == 8 and len(data) >= 50:          # full (has depth)
            ltp = struct.unpack_from("<f", data, 8)[0]
            rec = {"ltp": float(ltp), "kind": "full"}
        elif code == 6 and len(data) >= 16:          # prev close
            pc = struct.unpack_from("<f", data, 8)[0]
            rec = {"prev_close": float(pc), "kind": "prev"}
        if rec is None:
            return
        cur = self.prices.get(sid, {})
        cur.update(rec)
        cur["ts"] = time.time()
        self.prices[sid] = cur
        self.ticks += 1
        if self.on_tick:
            try:
                self.on_tick(sid, cur)
            except Exception:
                pass

    # ---- accessors -------------------------------------------------------
    def ltp(self, security_id):
        p = self.prices.get(int(security_id))
        return p.get("ltp") if p else None

    def snapshot(self) -> dict:
        return {"status": self.status, "ticks": self.ticks,
                "instruments": len(self.instruments),
                "prices": {k: v.get("ltp") for k, v in self.prices.items()}}
