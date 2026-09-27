"""Dhan index-options market data + contract resolution.

Two paths:
  1. LIVE option chain: POST /v2/optionchain
     and /v2/optionchain/expirylist return spot, strikes, premiums, IV, OI, and
     bid/ask per leg.

     This used to say "needs a SELF token", on the belief that TOTP-minted APP
     tokens were barred from market data.  Re-tested against a live APP token on
     2026-09-24: the chain returned full data (NIFTY spot 23446.8, 256 rows;
     BANKEX 63916.52, 322 rows, real bid/ask).  An APP token is sufficient, which
     means a fresh host can bootstrap itself from DHAN_PIN + DHAN_TOTP_SECRET
     with no browser-consent step.
  2. OFFLINE contract resolution (works now, from the Dhan scrip master):
     resolve a NIFTY/FINNIFTY option to (security_id, trading_symbol, lot_size)
     for order placement — no market-data token required.

Underlying IDs: NIFTY 13, BANKNIFTY 25, FINNIFTY 27, SENSEX 51.
"""
from __future__ import annotations

import csv
import json
import time
import urllib.error
import urllib.request
from datetime import datetime

from parallax.adapters.broker.dhan_auth import resolve_token
from parallax.adapters.env import env
from parallax.core.options.contracts import OptionContract

BASE = "https://api.dhan.co/v2"
IDX_SEGMENT = "IDX_I"
DEFAULT_SCRIP_MASTER = r"C:\PrOxyTradingTerminal\reports\security_id_list.csv"

#: Dhan underlying scrip ids for the index option chains.  SENSEX 51 and
#: BANKEX 69 were confirmed by the spot each returned (74,529 and 63,529).
UNDERLYING_IDS = {"NIFTY": 13, "BANKNIFTY": 25, "FINNIFTY": 27,
                  "SENSEX": 51, "BANKEX": 69}


def underlying_id(symbol: str) -> int:
    return UNDERLYING_IDS.get(str(symbol).upper(), 13)


#: Dhan answers a RATE-LIMITED option-chain request with HTTP 401 and
#: {"Data":{"810":"ClientId is invalid"}} -- not a 429 -- so a naive caller reads
#: it as a dead token and gives up on the entry.
#:
#: Measured on the VPS 2026-09-26: an afternoon of heavy use (a 966-call ladder
#: fetch) left /optionchain returning that 401 for hours, and the same token and
#: account kept working on /charts/* the whole time.  About 45 seconds of quiet
#: restored it.  Retrying with backoff turns "chain unavailable" -- which the
#: worker reports as a silent no-trade -- into a slightly slower entry.
#:
#: The client-id HEADER is also load-bearing: without it every call here 401s
#: even when the token is perfectly good.
RATE_LIMIT_CODES = (401, 429)
BACKOFF_S = (5.0, 20.0, 45.0)


def _post(path: str, payload: dict, token: str, client_id: str, timeout: int = 25,
          retries: int = 3) -> dict:
    for attempt in range(retries):
        req = urllib.request.Request(
            BASE + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "access-token": token, "client-id": client_id}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:200]
            except Exception:
                pass
            if e.code in RATE_LIMIT_CODES and attempt < retries - 1:
                time.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
                continue
            raise urllib.error.HTTPError(e.url, e.code, body or str(e.reason),
                                         e.headers, None)


def atm_straddle(symbol: str, expiry: str | None = None, token: str | None = None,
                 client_id: str | None = None) -> dict | None:
    """ATM straddle = the market's own expected move to expiry.

    CE + PE at the strike nearest spot.  No vol model is involved, which is the
    point: the straddle IS the expectation, and the shape research normalises
    short distance by it (sigma_room = short distance / straddle).  A 5-strike
    offset is a different bet on every index, and this is the ruler that shows
    by how much.
    """
    chain = fetch_option_chain(symbol, expiry=expiry, token=token, client_id=client_id)
    if not chain or not chain.get("rows"):
        return None
    from parallax.config.indices import spec as _spec
    step = float(_spec(symbol).step)
    spot = float(chain["spot"])
    atm = round(spot / step) * step
    ce = pe = None
    for r in chain["rows"]:
        if abs(float(r["strike"]) - atm) > 1e-6:
            continue
        if r["option_type"] == "CE":
            ce = r
        elif r["option_type"] == "PE":
            pe = r
    if not ce or not pe:
        return None
    strad = float(ce["ltp"]) + float(pe["ltp"])
    if strad <= 0:
        return None
    return {
        "symbol": str(symbol).upper(), "expiry": str(chain.get("expiry") or expiry or ""),
        "spot": spot, "atm": atm, "ce": float(ce["ltp"]), "pe": float(pe["ltp"]),
        "straddle": strad, "move_pct": 100.0 * strad / spot,
        "iv": (float(ce["iv"]) + float(pe["iv"])) / 2.0,
    }


def sigma_room(symbol: str, short_off: int, straddle: float) -> float:
    """How far the sold strikes sit, in units of the market's expected move."""
    from parallax.config.indices import spec as _spec
    if not straddle or straddle <= 0:
        return 0.0
    return (int(short_off) * float(_spec(symbol).step)) / float(straddle)


def _auth(token: str | None, client_id: str | None):
    """Resolve the best token (saved SELF token preferred) + client id."""
    cid = client_id or env("DHAN_CLIENT_ID")
    if token is None:
        token, _src = resolve_token(cid, env("DHAN_ACCESS_TOKEN"),
                                    env("DHAN_PIN"), env("DHAN_TOTP_SECRET"))
    return cid, token


def fetch_expiries(symbol: str, token: str | None = None,
                   client_id: str | None = None) -> list[str]:
    """Expiry list for an index underlying (needs Data API subscription)."""
    client_id, token = _auth(token, client_id)
    body = _post("/optionchain/expirylist",
                 {"UnderlyingScrip": underlying_id(symbol), "UnderlyingSeg": IDX_SEGMENT},
                 token, client_id)
    return sorted((body or {}).get("data") or [])


def fetch_option_chain(symbol: str, expiry: str | None = None,
                       token: str | None = None,
                       client_id: str | None = None) -> dict | None:
    """Live option chain.  Returns
    {underlying, expiry, spot, rows:[{strike, option_type, security_id, ltp,
    oi, volume, iv, bid, ask}]} or None on failure."""
    client_id, token = _auth(token, client_id)
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
                g = leg.get("greeks") or {}
                rows.append({
                    "strike": strike, "option_type": otype.upper(),
                    "security_id": leg.get("security_id"), "ltp": float(ltp),
                    "oi": int(leg.get("oi") or 0), "volume": int(leg.get("volume") or 0),
                    "iv": iv, "bid": float(leg.get("top_bid_price") or 0.0),
                    "ask": float(leg.get("top_ask_price") or 0.0),
                    "delta": float(g.get("delta") or 0.0),
                    "theta": float(g.get("theta") or 0.0),
                    "gamma": float(g.get("gamma") or 0.0),
                    "vega": float(g.get("vega") or 0.0),
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
