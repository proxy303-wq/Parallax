"""Dhan broker adapter — NIFTY index futures (NSE F&O) via the dhanhq SDK.

Real order execution on Dhan using DHAN_CLIENT_ID + DHAN_ACCESS_TOKEN from the
shared .env chain.  The NIFTY futures contract (securityId + trading symbol +
lot size) is resolved from Dhan's scrip master (SEM_INSTRUMENT_NAME == "FUTIDX",
near-month, non-FPI) — the same resolution the verified proxy.dhan_broker /
futures_engine use.  Quantities are converted lots -> units using the resolved
lot size (NIFTY = 65 units/lot).

Safety: dry_run=True by default — place/cancel/modify return acks WITHOUT
touching the Dhan API.  Set dry_run=False (or run the executive in a live mode)
to send real orders.
"""
from __future__ import annotations

import csv
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from parallax.contracts import (
    AccountState, BrokerReconciliation, CancelAck, ModifyAck,
    ModifyOrderRequest, Order, OrderAck, OrderStatus, Position, Quote, Side,
    ValidatedOrderIntent,
)
from parallax.adapters.broker.base import BrokerAdapter
from parallax.adapters.env import env
from .dhan_auth import resolve_token

DEFAULT_SCRIP_MASTER = r"C:\PrOxyTradingTerminal\reports\security_id_list.csv"
# repo-local cache: the Windows path above does not exist on the VPS, and without
# a scrip master resolve_contract() cannot produce a securityId, so the futures
# leg silently places nothing at all.
_REPO_ROOT = Path(__file__).resolve().parents[3]
LOCAL_SCRIP_MASTER = str(_REPO_ROOT / "data" / "security_id_list.csv")
SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
SCRIP_MAX_AGE_DAYS = 7.0


def default_scrip_master() -> str:
    """Explicit override -> legacy Windows file -> repo-local cache."""
    p = os.environ.get("PARALLAX_SCRIP_MASTER") or ""
    if p:
        return p
    if os.path.exists(DEFAULT_SCRIP_MASTER):
        return DEFAULT_SCRIP_MASTER
    return LOCAL_SCRIP_MASTER


def ensure_scrip_master(path: str, url: str = SCRIP_MASTER_URL,
                        max_age_days: float = SCRIP_MAX_AGE_DAYS) -> str:
    """Return a usable scrip-master path, downloading Dhan's copy when stale."""
    if os.path.exists(path) and path != LOCAL_SCRIP_MASTER:
        return path                       # an explicit / legacy file always wins
    fresh = (os.path.exists(path) and
             (time.time() - os.path.getmtime(path)) < max_age_days * 86400)
    if fresh:
        return path
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "parallax/1.0"})
    with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as fh:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
    os.replace(tmp, path)
    return path

# Dhan order status -> PARALLAX OrderStatus
DHAN_STATUS_MAP = {
    "TRANSIT": OrderStatus.NEW,
    "PENDING": OrderStatus.NEW,
    "TRIGGERED": OrderStatus.NEW,
    "CLOSED": OrderStatus.FILLED,
    "TRADED": OrderStatus.FILLED,
    "PART_TRADED": OrderStatus.PARTIALLY_FILLED,
    "REJECTED": OrderStatus.REJECTED,
    "CANCELLED": OrderStatus.CANCELLED,
    "EXPIRED": OrderStatus.EXPIRED,
}


def _map_status(s: str) -> OrderStatus:
    return DHAN_STATUS_MAP.get(str(s or "").upper(), OrderStatus.NEW)


class DhanBroker(BrokerAdapter):
    def __init__(self, client_id=None, access_token=None, dry_run=True,
                 instrument="NIFTY", scrip_master=None,
                 product_type="INTRADAY", exchange_segment="NSE_FNO",
                 connect_on_init=True):
        self.client_id = client_id or env("DHAN_CLIENT_ID")
        self.access_token = access_token or env("DHAN_ACCESS_TOKEN")
        self.pin = env("DHAN_PIN")
        self.totp_secret = env("DHAN_TOTP_SECRET")
        self.dry_run = bool(dry_run)
        self.instrument_name = instrument
        self.scrip_master = scrip_master or default_scrip_master()
        self.product_type = product_type
        self.exchange_segment = exchange_segment
        self._api = None
        self._contract: tuple | None = None
        self.token_source = ""
        self._auth_error = ""
        if connect_on_init:
            self.connect()

    @property
    def configured(self) -> bool:
        return bool(self.client_id and (self.access_token or (self.pin and self.totp_secret)))

    def connect(self) -> bool:
        if not self.client_id:
            self._auth_error = "DHAN_CLIENT_ID missing"
            return False
        tok, src = resolve_token(self.client_id, self.access_token, self.pin,
                                 self.totp_secret, notify=lambda m: None)
        if not tok:
            self._auth_error = src
            return False
        self.access_token = tok
        self.token_source = src
        self._init_sdk()
        return True

    def _init_sdk(self) -> None:
        from dhanhq import DhanContext, dhanhq
        self._api = dhanhq(DhanContext(self.client_id, self.access_token))

    def _api_client(self):
        if self._api is None and not self.connect():
            raise RuntimeError(self._auth_error or "Dhan not connected")
        return self._api

    # ---- contract resolution -------------------------------------------
    def resolve_contract(self) -> tuple[int, str, str, float]:
        """(security_id, trading_symbol, expiry, lot) for the near-month
        NIFTY index future from the Dhan scrip master."""
        if self._contract is not None:
            return self._contract
        rows: list[tuple[str, str, str, str]] = []
        path = self.scrip_master
        if not os.path.exists(path):
            path = ensure_scrip_master(path)
        try:
            with open(path, encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    if (r.get("SEM_INSTRUMENT_NAME") or "") != "FUTIDX":
                        continue
                    sym = r.get("SEM_TRADING_SYMBOL") or ""
                    if not sym.upper().startswith(self.instrument_name.upper() + "-"):
                        continue
                    if "FPI" in sym.upper():
                        continue
                    expiry = (r.get("SEM_EXPIRY_DATE") or "")[:10]
                    if not expiry:
                        continue
                    rows.append((expiry, sym, r.get("SEM_SMST_SECURITY_ID") or "",
                                 r.get("SEM_LOT_UNITS") or ""))
        except OSError:
            rows = []
        if not rows:
            raise RuntimeError(
                f"no FUTIDX contract for {self.instrument_name} in {path}")
        rows.sort(key=lambda x: x[0])
        today = datetime.now().date()
        for expiry, sym, sid, lot in rows:
            try:
                if datetime.strptime(expiry, "%Y-%m-%d").date() >= today:
                    self._contract = (int(sid), sym, expiry, float(lot or 0))
                    return self._contract
            except ValueError:
                continue
        # all expiries in the past: use the last one
        expiry, sym, sid, lot = rows[-1]
        self._contract = (int(sid), sym, expiry, float(lot or 0))
        return self._contract

    @property
    def lot_size(self) -> float:
        _sid, _sym, _exp, lot = self.resolve_contract()
        return lot or 1.0

    # ---- BrokerAdapter --------------------------------------------------
    def get_account(self) -> AccountState:
        if not self.configured:
            return AccountState(cash=0.0, equity=0.0, buying_power=0.0,
                                currency="INR")
        res = self._api_client().get_fund_limits()
        data = (res or {}).get("data") or {}
        available = float(data.get("availabelBalance") or data.get("availableBalance") or 0.0)
        utilized = float(data.get("utilizedAmount") or 0.0)
        collateral = float(data.get("collateralAmount") or 0.0)
        equity = available + utilized + collateral
        return AccountState(cash=available, equity=equity,
                            buying_power=available,
                            margin_used=utilized + collateral,
                            unrealized_pnl=0.0, realized_pnl=0.0,
                            currency="INR")

    def get_positions(self) -> list[Position]:
        if not self.configured:
            return []
        res = self._api_client().get_positions()
        out: list[Position] = []
        for r in (res or {}).get("data") or []:
            raw_side = str(r.get("positionType") or r.get("transactionType") or "BUY").upper()
            side = Side.BUY if raw_side in ("BUY", "LONG") else Side.SELL
            qty = float(r.get("netQty") or r.get("quantity")
                        or r.get("netQuantity") or 0.0)
            entry = float(r.get("averagePrice") or r.get("avgPrice") or 0.0)
            mark = float(r.get("lastPrice") or entry)
            out.append(Position(
                instrument=str(r.get("tradingSymbol", self.instrument_name)),
                side=side, quantity=abs(qty), avg_entry=entry, current_price=mark,
                unrealized_pnl=float(r.get("unrealizedProfit") or 0.0)))
        return out

    def get_open_orders(self) -> list[Order]:
        if not self.configured:
            return []
        res = self._api_client().dhan_http.get("/orders")
        out: list[Order] = []
        for r in (res or {}).get("data") or []:
            raw_side = str(r.get("transactionType") or "BUY").upper()
            out.append(Order(
                order_id=str(r.get("orderId", "")), intent_id="", decision_id="",
                instrument=str(r.get("tradingSymbol", "")),
                side=Side.BUY if raw_side == "BUY" else Side.SELL,
                quantity=float(r.get("quantity") or 0.0),
                status=_map_status(r.get("orderStatus")),
                avg_price=float(r.get("averageTradedPrice") or r.get("price") or 0.0) or None))
        return out

    def get_quote(self, instrument: str) -> Quote:
        try:
            sid, _sym, _exp, _lot = self.resolve_contract()
            api = self._api_client()
            last = 0.0
            try:
                res = api.quote_data(security_id=sid,
                                     exchange_segment=self.exchange_segment)
                d = (res or {}).get("data") or res or {}
                last = float(d.get("last_price") or d.get("LTP") or d.get("lastPrice") or 0.0)
            except Exception:
                last = 0.0
            return Quote(instrument, datetime.now(timezone.utc), last, last, last)
        except Exception:
            return Quote(instrument, datetime.now(timezone.utc), 0.0, 0.0, 0.0)

    def place_order(self, intent: ValidatedOrderIntent) -> OrderAck:
        i = intent.intent
        if not intent.valid:
            return OrderAck("", i.intent_id, OrderStatus.REJECTED,
                            message="; ".join(intent.errors))
        if not self.configured:
            return OrderAck("", i.intent_id, OrderStatus.REJECTED,
                            message="Dhan not configured (DHAN_CLIENT_ID/ACCESS_TOKEN)")
        sid, tsym, _exp, lot = self.resolve_contract()
        units = int(round(i.quantity * lot))
        otype = "MARKET" if i.order_type.value == "MARKET" else "LIMIT"
        if self.dry_run:
            return OrderAck("DRY", i.intent_id, OrderStatus.NEW,
                            message=f"dry_run: {i.side.value} {units} units {tsym} "
                                    f"(sid {sid}) NOT sent")
        payload = {
            "dhanClientId": self.client_id,
            "correlationId": (i.client_tag or "PARALLAX")[:30],
            "transactionType": i.side.value.upper(),
            "exchangeSegment": self.exchange_segment,
            "productType": self.product_type,
            "orderType": otype,
            "validity": "DAY",
            "tradingSymbol": tsym,
            "securityId": str(sid),
            "quantity": units,
            "disclosedQuantity": 0,
            "price": 0.0 if otype == "MARKET" else round(float(i.price or 0.0), 2),
            "triggerPrice": round(float(i.trigger_price or 0.0), 2),
            "afterMarketOrder": False,
            "amoTime": "",
            "boProfitValue": None,
            "boStopLossValue": None,
            "tag": "PARALLAX",
        }
        try:
            res = self._api_client().dhan_http.post("/orders", payload)
        except Exception as e:
            return OrderAck("", i.intent_id, OrderStatus.REJECTED,
                            message=str(e)[:200])
        if not isinstance(res, dict):
            return OrderAck("", i.intent_id, OrderStatus.REJECTED,
                            message=f"unexpected Dhan response: {str(res)[:120]}")
        if res.get("errorCode") or res.get("errorType"):
            return OrderAck("", i.intent_id, OrderStatus.REJECTED,
                            message=str(res.get("errorMessage") or res.get("errorCode"))[:200])
        oid = str(res.get("orderId", ""))
        return OrderAck(oid, i.intent_id, _map_status(res.get("orderStatus")),
                        message="submitted")

    # ---- index-options support -----------------------------------------
    def basket_margin(self, legs: list) -> dict:
        """EXACT margin for a multi-leg basket, from Dhan's multi calculator.

        leg = {security_id, transaction_type, quantity, price} plus optional
        exchange_segment / product_type.

        This is the only endpoint that prices a hedged basket.  The single-leg
        /margincalculator returns the NAKED requirement, which for a 15-lot
        NIFTY condor is ~Rs 39 lakh against a real Rs 19.4 lakh - a factor of
        two, and the difference between "fits an 8L account" and "does not".

        The response splits the requirement, and the split is the point:

            spanMargin    <- the hedge benefit lands here (Rs 97,880 vs
                             Rs 20.9 lakh naked, a ~200x reduction)
            exposure      <- NOT netted at all; identical with or without the
                             long legs, and ~94% of the total
            hedgeBenefit  <- reported separately, 0.0 on this account
        """
        payload = {
            "dhanClientId": self.client_id,
            "scripList": [{
                "securityId": str(l["security_id"]),
                "exchangeSegment": l.get("exchange_segment", self.exchange_segment),
                "transactionType": str(l["transaction_type"]).upper(),
                "quantity": int(l["quantity"]),
                "productType": l.get("product_type", self.product_type),
                "price": float(l["price"]),
            } for l in legs],
        }
        try:
            return self._api_client().dhan_http.post(
                "/margincalculator/multi", payload) or {}
        except Exception as e:
            return {"error": str(e)[:160]}

    def option_margin(self, contract, side: str, qty_lots: int, price: float) -> float:
        """Dhan margin for one option leg (sell = SPAN+exposure, buy = premium).
        contract: OptionContract.  Returns total margin in INR."""
        if not self.configured:
            return 0.0
        units = int(qty_lots * contract.lot_size)
        try:
            res = self._api_client().margin_calculator(
                security_id=str(contract.security_id),
                exchange_segment=self.exchange_segment,
                transaction_type=str(side).upper(), quantity=units,
                product_type=self.product_type, price=round(float(price), 2),
                trigger_price=0)
            d = (res or {}).get("data") or res or {}
            return float(d.get("totalMargin") or 0.0)
        except Exception:
            return 0.0

    def place_option_order(self, contract, side: str, qty_lots: int,
                           order_type: str = "MARKET",
                           price: float | None = None) -> OrderAck:
        """Place an option order (resolved OptionContract).  dry_run-safe."""
        units = int(qty_lots * contract.lot_size)
        otype = str(order_type).upper()
        if otype not in ("MARKET", "LIMIT"):
            otype = "MARKET"
        if self.dry_run:
            return OrderAck("DRY", "", OrderStatus.NEW,
                            message=f"dry_run: {side.upper()} {units} units "
                                    f"{contract.trading_symbol} NOT sent")
        payload = {
            "dhanClientId": self.client_id,
            "correlationId": "PARALLAXOPT",
            "transactionType": str(side).upper(),
            "exchangeSegment": self.exchange_segment,
            "productType": self.product_type,
            "orderType": otype,
            "validity": "DAY",
            "tradingSymbol": contract.trading_symbol,
            "securityId": str(contract.security_id),
            "quantity": units,
            "disclosedQuantity": 0,
            "price": 0.0 if otype == "MARKET" else round(float(price or 0.0), 2),
            "triggerPrice": 0.0,
            "afterMarketOrder": False,
            "amoTime": "",
            "boProfitValue": None,
            "boStopLossValue": None,
            "tag": "PARALLAX",
        }
        try:
            res = self._api_client().dhan_http.post("/orders", payload)
        except Exception as e:
            return OrderAck("", "", OrderStatus.REJECTED, message=str(e)[:200])
        if not isinstance(res, dict) or res.get("errorCode") or res.get("errorType"):
            return OrderAck("", "", OrderStatus.REJECTED,
                            message=str(res.get("errorMessage") or res.get("errorCode"))[:200])
        return OrderAck(str(res.get("orderId", "")), "", _map_status(res.get("orderStatus")),
                        message="submitted")

    def cancel_order(self, order_id: str) -> CancelAck:
        if self.dry_run:
            return CancelAck(order_id, OrderStatus.CANCELLED, "dry_run")
        try:
            self._api_client().cancel_order(order_id)
            return CancelAck(order_id, OrderStatus.CANCELLED, "cancel requested")
        except Exception as e:
            return CancelAck(order_id, OrderStatus.REJECTED, str(e)[:200])

    def modify_order(self, request: ModifyOrderRequest) -> ModifyAck:
        if self.dry_run:
            return ModifyAck(request.order_id, OrderStatus.NEW, "dry_run")
        return ModifyAck(request.order_id, OrderStatus.REJECTED,
                         "modify not wired (use cancel + re-place)")

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
