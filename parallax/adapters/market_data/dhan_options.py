"""Dhan index-options market data + contract resolution.

Two paths:
  1. LIVE option chain (needs a SELF token — market data): POST /v2/optionchain
     and /v2/optionchain/expirylist return spot, strikes, premiums, IV, OI, and
     bid/ask per leg.
  2. OFFLINE contract resolution (works now, from the Dhan scrip master):
     resolve a NIFTY/FINNIFTY option to (security_id, trading_symbol, lot_size)
     for order placement — no market-data token required.

Underlying IDs: NIFTY 13, BANKNIFTY 25, FINNIFTY 27, SENSEX 51.
"""
from __future__ import annotations

import csv
import json
import urllib.request
from datetime import datetime

from parallax.adapters.broker.dhan_auth import resolve_token
from parallax.adapters.env import env
from parallax.core.options.contracts import OptionContract

BASE = "https://api.dhan.co/v2"
IDX_SEGMENT = "IDX_I"
DEFAULT_SCRIP_MASTER = r"C:\PrOxyTradingTerminal\reports\security_id_list.csv"

UNDERLYING_IDS = {"NIFTY": 13, "BANKNIFTY": 25, "FINNIFTY": 27, "SENSEX": 51}


def underlying_id(symbol: str) -> int:
    return UNDERLYING_IDS.get(str(symbol).upper(), 13)


def _post(path: str, payload: dict, token: str, client_id: str, timeout: int = 25) -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "access-token": token, "client-id": client_id}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def fetch_expiries(symbol: str, token: str | None = None,
                   client_id: str | None = None) -> list[str]:
    """Expiry list for an index underlying (needs a SELF token)."""
    client_id = client_id or env("DHAN_CLIENT_ID")
    token = token or env("DHAN_ACCESS_TOKEN")
    body = _post("/optionchain/expirylist",
                 {"UnderlyingScrip": underlying_id(symbol), "UnderlyingSeg": IDX_SEGMENT},
                 token, client_id)
    return sorted((body or {}).get("data") or [])


def fetch_option_chain(symbol: str, expiry: str | None = None,
                       token: str | None = None,
                       client_id: str | None = None) -> dict | None:
    """Live option chain (needs a SELF token).  Returns
    {underlying, expiry, spot, rows:[{strike, option_type, security_id, ltp,
    oi, volume, iv, bid, ask}]} or None on failure."""
    client_id = client_id or env("DHAN_CLIENT_ID")
    token = token or env("DHAN_ACCESS_TOKEN")
    try:
        if not expiry:
            dates = fetch_expiries(symbol, token, client_id)
            today = datetime.now().date()
            cands = [d for d in dates if datetime.strptime(d, "%Y-%m-%d").date() >= today]
            expiry = (cands or dates)[0] if dates else None
        body = _post("/optionchain",
                     {"UnderlyingScrip": underlying_id(symbol), "UnderlyingSeg": IDX_SEGMENT,
                      "Expiry": str(expiry)}, token, client_id)
        data = body.get("data") or {}
        spot = float(data.get("last_price") or 0.0)
        oc = data.get("oc") or {}
        rows = []
        for strike_str, legs in oc.items():
            strike = float(strike_str)
            for otype in ("ce", "pe"):
                leg = legs.get(otype) or {}
                ltp = leg.get("last_price")
                if not ltp:
                    continue
                iv = float(leg.get("implied_volatility") or 0.0)
                if iv > 1.0:
                    iv = iv / 100.0
                rows.append({
                    "strike": strike, "option_type": otype.upper(),
                    "security_id": leg.get("security_id"), "ltp": float(ltp),
                    "oi": int(leg.get("oi") or 0), "volume": int(leg.get("volume") or 0),
                    "iv": iv, "bid": float(leg.get("top_bid_price") or 0.0),
                    "ask": float(leg.get("top_ask_price") or 0.0),
                })
        return {"underlying": symbol, "expiry": str(expiry), "spot": spot, "rows": rows}
    except Exception:
        return None


# ---- offline contract resolution (scrip master, no market-data token) -----

def _iter_opt_rows(symbol: str, scrip_master: str = DEFAULT_SCRIP_MASTER):
    prefix = str(symbol).upper() + "-"
    with open(scrip_master, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if (r.get("SEM_INSTRUMENT_NAME") or "") != "OPTIDX":
                continue
            sym = (r.get("SEM_TRADING_SYMBOL") or "")
            if not sym.upper().startswith(prefix):
                continue
            if "FPI" in sym.upper():
                continue
            yield r


def resolve_contract(symbol: str, strike: float, option_type: str,
                     expiry: str, scrip_master: str = DEFAULT_SCRIP_MASTER) -> OptionContract | None:
    """Resolve one option to its Dhan contract (offline, for order placement)."""
    strike_s = f"{float(strike):.0f}" if float(strike) == int(float(strike)) else f"{float(strike):.2f}"
    otype = str(option_type).upper()
    for r in _iter_opt_rows(symbol, scrip_master):
        if (r.get("SEM_OPTION_TYPE") or "").upper() != otype:
            continue
        if (r.get("SEM_EXPIRY_DATE") or "")[:10] != str(expiry)[:10]:
            continue
        if abs(float(r.get("SEM_STRIKE_PRICE") or 0.0) - float(strike)) > 1e-6:
            continue
        return OptionContract(
            symbol=str(symbol).upper(), strike=float(strike), expiry=str(expiry)[:10],
            option_type=otype, lot_size=int(float(r.get("SEM_LOT_UNITS") or 0)),
            security_id=int(r.get("SEM_SMST_SECURITY_ID") or 0),
            trading_symbol=r.get("SEM_TRADING_SYMBOL") or "",
            strike_step=50.0)   # NIFTY/FINNIFTY strike ladder (not SEM_TICK_SIZE)
    return None


def available_strikes(symbol: str, expiry: str,
                      scrip_master: str = DEFAULT_SCRIP_MASTER) -> list[float]:
    """Sorted list of strikes for a symbol+expiry (offline)."""
    out = set()
    for r in _iter_opt_rows(symbol, scrip_master):
        if (r.get("SEM_EXPIRY_DATE") or "")[:10] == str(expiry)[:10]:
            try:
                out.add(float(r.get("SEM_STRIKE_PRICE") or 0.0))
            except ValueError:
                pass
    return sorted(out)
