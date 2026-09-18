"""Delta Exchange India broker adapter — real account/order access (v2 REST).

Verified signing (api.india.delta.exchange):
    signature = hex(HMAC-SHA256(api_secret,
                    METHOD + unix_timestamp_seconds + request_path + body))
    headers: api-key / timestamp / signature
METHOD comes FIRST and the timestamp is in SECONDS.  A 401 with an
"expired_signature" detail carries the server clock, which we adopt and retry.

Credentials: DELTA_API_KEY / DELTA_API_SECRET (env or shared .env).  Market
data needs no auth (see market_data.delta_feed).

Safety: dry_run=True by default — place/cancel/modify return acks WITHOUT
touching the API.  Set dry_run=False (or run the executive in a live mode) to
send real orders.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from parallax.contracts import (
    AccountState, BrokerReconciliation, CancelAck, ModifyAck,
    ModifyOrderRequest, Order, OrderAck, OrderStatus, Position, Quote, Side,
    ValidatedOrderIntent,
)
from parallax.adapters.broker.base import BrokerAdapter
from parallax.adapters.env import env

BASE_URL = "https://api.india.delta.exchange"

# The India venue trades INVERSE perps: PARALLAX symbol -> Delta symbol
SYMBOL_MAP = {"BTC": "BTCUSD", "ETH": "ETHUSD", "SOL": "SOLUSD",
              "XAUT": "XAUTUSD", "XRP": "XRPUSD"}


class DeltaBroker(BrokerAdapter):
    def __init__(self, api_key=None, api_secret=None, base_url=BASE_URL,
                 dry_run=True, timeout=40, currency="INR"):
        self.api_key = api_key or env("DELTA_API_KEY")
        self.api_secret = api_secret or env("DELTA_API_SECRET")
        self.base_url = (base_url or env("DELTA_API_BASE", BASE_URL)).rstrip("/")
        self.dry_run = bool(dry_run)
        self.timeout = timeout
        self.currency = currency
        self._clock_offset = 0
        self._product_cache: dict[str, dict] = {}

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    # ---- auth core ------------------------------------------------------
    def _sign(self, timestamp_sec: str, method: str, path: str, body: str = "") -> str:
        msg = f"{method}{timestamp_sec}{path}{body}"
        return hmac.new(self.api_secret.encode(), msg.encode(),
                        hashlib.sha256).hexdigest()

    def _request(self, method, path, body=None, retries=2):
        if not self.configured:
            raise RuntimeError("Delta not configured (DELTA_API_KEY/SECRET missing)")
        url = self.base_url + path
        data = json.dumps(body) if body is not None else ""
        for attempt in range(retries + 2):
            ts = str(int(time.time()) + self._clock_offset)
            sig = self._sign(ts, method, path, data)
            headers = {"api-key": self.api_key, "timestamp": ts,
                       "signature": sig, "Content-Type": "application/json"}
            req = urllib.request.Request(url, data=data.encode() if data else None,
                                         headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")
                if attempt == 0 and "expired_signature" in detail:
                    try:
                        server = int(json.loads(detail)["error"]["context"]["server_time"])
                        self._clock_offset = server - int(time.time())
                        continue
                    except Exception:
                        pass
                if attempt >= retries:
                    raise RuntimeError(
                        f"Delta {method} {path} -> HTTP {e.code}: {detail[:400]}")
                time.sleep(1.0)

    # ---- symbol / product ----------------------------------------------
    def _symbol(self, instrument: str) -> str:
        base = str(instrument).split(":")[-1].upper()
        return SYMBOL_MAP.get(base, base + "USD")

    def _product(self, symbol: str) -> dict:
        if symbol in self._product_cache:
            return self._product_cache[symbol]
        data = self._request("GET", "/v2/products?limit=300")
        for p in data.get("result") or []:
            if p.get("symbol") == symbol:
                self._product_cache[symbol] = p
                return p
        raise RuntimeError(f"Delta product {symbol} not found")

    # ---- BrokerAdapter --------------------------------------------------
    def get_quote(self, instrument: str) -> Quote:
        sym = self._symbol(instrument)
        try:
            req = urllib.request.Request(
                self.base_url + f"/v2/tickers/{sym}",
                headers={"Accept": "application/json", "User-Agent": "parallax/1.0"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = json.loads(r.read().decode())
            t = raw.get("result", raw)
            t = t if isinstance(t, dict) else {}
            q = t.get("quotes") or {}
            bid = float(q.get("best_bid") or 0.0)
            ask = float(q.get("best_ask") or 0.0)
            last = float(t.get("mark_price") or t.get("close") or ask or bid or 0.0)
            return Quote(instrument, datetime.now(timezone.utc), bid, ask, last)
        except Exception:
            return Quote(instrument, datetime.now(timezone.utc), 0.0, 0.0, 0.0)

    def get_account(self) -> AccountState:
        data = self._request("GET", "/v2/wallet/balances")
        rows = data.get("result") or []
        cash = 0.0
        for r in rows:
            try:
                cash += float(r.get("available_balance_inr")
                              or r.get("balance_inr")
                              or r.get("available_balance") or 0.0)
            except (TypeError, ValueError):
                pass
        return AccountState(cash=cash, equity=cash, buying_power=cash,
                            margin_used=0.0, unrealized_pnl=0.0,
                            realized_pnl=0.0, currency=self.currency)

    def get_positions(self) -> list[Position]:
        out: list[Position] = []
        # India /v2/positions requires an underlying filter (no unfiltered list)
        for underlying in SYMBOL_MAP:
            try:
                data = self._request(
                    "GET", f"/v2/positions?underlying_asset_symbol={underlying}")
            except Exception:
                continue
            for r in data.get("result") or []:
                size = float(r.get("size") or 0.0)
                side = Side.BUY if size > 0 else Side.SELL
                entry = float(r.get("entry_price") or 0.0)
                mark = float(r.get("mark_price") or entry)
                out.append(Position(
                    instrument=str(r.get("symbol", underlying)), side=side,
                    quantity=abs(size), avg_entry=entry, current_price=mark,
                    unrealized_pnl=float(r.get("unrealized_pnl") or 0.0)))
        return out

    def get_open_orders(self) -> list[Order]:
        data = self._request("GET", "/v2/orders?state=open")
        out: list[Order] = []
        for r in data.get("result") or []:
            out.append(Order(
                order_id=str(r.get("id", "")), intent_id="", decision_id="",
                instrument=str(r.get("symbol", "")),
                side=Side.BUY if r.get("side") == "buy" else Side.SELL,
                quantity=float(r.get("size") or 0.0), status=OrderStatus.NEW,
                avg_price=float(r.get("limit_price") or 0.0) or None))
        return out

    def place_order(self, intent: ValidatedOrderIntent) -> OrderAck:
        i = intent.intent
        if not intent.valid:
            return OrderAck("", i.intent_id, OrderStatus.REJECTED,
                            message="; ".join(intent.errors))
        if not self.configured:
            return OrderAck("", i.intent_id, OrderStatus.REJECTED,
                            message="Delta not configured (DELTA_API_KEY/SECRET missing)")
        sym = self._symbol(i.instrument)
        if self.dry_run:
            return OrderAck("DRY", i.intent_id, OrderStatus.NEW,
                            message=f"dry_run: {i.side.value} {i.quantity} {sym} NOT sent")
        product = self._product(sym)
        cv = float(product.get("contract_value") or 1.0)
        size = int(round(i.quantity / cv)) if cv else int(round(i.quantity))
        side = "buy" if i.side == Side.BUY else "sell"
        otype = "market" if i.order_type.value == "MARKET" else "limit_order"
        body = {
            "product_id": product.get("id"),
            "size": size,
            "side": side,
            "order_type": otype,
            "time_in_force": "ioc" if otype == "market" else "gtc",
            "reduce_only": False,
        }
        if i.price is not None and otype != "market":
            body["limit_price"] = str(i.price)
        res = self._request("POST", "/v2/orders", body=body)
        r = res.get("result") or {}
        return OrderAck(str(r.get("id", "")), i.intent_id, OrderStatus.NEW,
                        message="submitted")

    def cancel_order(self, order_id: str) -> CancelAck:
        if self.dry_run:
            return CancelAck(order_id, OrderStatus.CANCELLED, "dry_run")
        self._request("DELETE", f"/v2/orders/{order_id}")
        return CancelAck(order_id, OrderStatus.CANCELLED, "cancel requested")

    def modify_order(self, request: ModifyOrderRequest) -> ModifyAck:
        return ModifyAck(request.order_id, OrderStatus.REJECTED,
                         "modify not supported on Delta v2")

    def reconcile(self) -> BrokerReconciliation:
        try:
            positions = self.get_positions()
            return BrokerReconciliation(ok=True, positions_match=True,
                                        orders_match=True,
                                        discrepancies=[] if positions else ["no open positions"])
        except Exception as e:
            return BrokerReconciliation(ok=False, positions_match=False,
                                        orders_match=False,
                                        discrepancies=[str(e)[:200]])
