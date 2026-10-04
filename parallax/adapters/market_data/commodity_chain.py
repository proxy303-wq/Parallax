"""MCX commodity option chain via the /optionchain endpoint.

Confirmed: Dhan's /optionchain serves MCX commodity options when given the
futures contract as the underlying - UnderlyingScrip = the FUTCOM security id,
UnderlyingSeg = "MCX_COMM".  It returns the full strike grid with bid/ask, IV,
OI, volume and greeks in ONE call, exactly like the index chain, so there is no
need to assemble strikes from the scrip master and price them via LTP.

The futures id is still resolved from the scrip master (the future expiring
just AFTER the option expiry is the one the option settles into).
"""
from __future__ import annotations

import csv
import json
import os
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime

from parallax.adapters.broker.dhan_auth import active_token
from parallax.config.commodities import (FUTURE_INSTRUMENT, MCX_SEGMENT,
                                         OPTION_INSTRUMENT, spec)

OPTIONCHAIN_URL = "https://api.dhan.co/v2/optionchain"
LTP_URL = "https://api.dhan.co/v2/marketfeed/ltp"
BATCH = 60

SCRIP_MASTERS = (
    os.environ.get("PARALLAX_SCRIP_MASTER", ""),
    "/opt/parallax/data/api-scrip-master.csv",
    "C:/PrOxyTradingTerminal/reports/security_id_list.csv",
)

_symbol_cache: dict[str, tuple[dict, dict]] = {}


def find_scrip_master() -> str:
    for p in SCRIP_MASTERS:
        if p and os.path.exists(p):
            return p
    raise FileNotFoundError("no scrip master at " + " or ".join(x for x in SCRIP_MASTERS if x))


def _load_symbol(symbol: str) -> tuple[dict, dict]:
    """(futures {expiry: id}, options {expiry: {strike: {CE/PE: id}}}), cached."""
    base = spec(symbol).symbol
    if base in _symbol_cache:
        return _symbol_cache[base]
    futures: dict[str, int] = {}
    options: dict[str, dict] = defaultdict(dict)
    with open(find_scrip_master(), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if (r.get("SEM_EXM_EXCH_ID") or "") != "MCX":
                continue
            sym = r.get("SEM_TRADING_SYMBOL") or ""
            if sym.split("-")[0] != base:
                continue
            exp = (r.get("SEM_EXPIRY_DATE") or "")[:10]
            inst = r.get("SEM_INSTRUMENT_NAME") or ""
            try:
                sid = int(r.get("SEM_SMST_SECURITY_ID") or 0)
            except ValueError:
                continue
            if inst == FUTURE_INSTRUMENT:
                futures[exp] = sid
            elif inst == OPTION_INSTRUMENT:
                try:
                    strike = float(r.get("SEM_STRIKE_PRICE") or 0)
                except ValueError:
                    continue
                if strike <= 0:
                    continue
                otype = (r.get("SEM_OPTION_TYPE") or "").upper()
                options[exp].setdefault(strike, {"CE": None, "PE": None})[otype] = sid
    _symbol_cache[base] = (futures, options)
    return futures, options


def list_expiries(symbol: str) -> list[str]:
    _fut, options = _load_symbol(symbol)
    return sorted(options)


def nearest_expiry(symbol: str, today: str | None = None) -> str | None:
    exps = list_expiries(symbol)
    if not exps:
        return None
    today = today or datetime.now().date().isoformat()
    for d in exps:
        if d >= today:
            return d
    return exps[-1]


def future_id(symbol: str, expiry: str) -> int | None:
    """The FUTCOM contract the option settles into (nearest expiry >= option expiry)."""
    futures, _options = _load_symbol(symbol)
    later = sorted(e for e in futures if e >= expiry)
    return futures[later[0]] if later else (futures[sorted(futures)[-1]] if futures else None)


def contracts(symbol: str, expiry: str) -> tuple[dict, int | None]:
    """({strike: {"CE": id, "PE": id}}, futures_id) - kept for the backtest."""
    _futures, options = _load_symbol(symbol)
    return options.get(expiry, {}), future_id(symbol, expiry)


def fetch_ltp(ids, token: str, client_id: str, retries: int = 3) -> dict[int, float]:
    """LTP map {id: price} for MCX security ids, batched and 429-tolerant."""
    out = {}
    ids = [int(i) for i in ids if i]
    for i in range(0, len(ids), BATCH):
        chunk = ids[i:i + BATCH]
        body = json.dumps({MCX_SEGMENT: chunk}).encode()
        for attempt in range(retries):
            req = urllib.request.Request(LTP_URL, data=body,
                                         headers={"access-token": token, "client-id": client_id,
                                                  "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    d = json.loads(r.read().decode())
                seg = (d.get("data") or {}).get(MCX_SEGMENT) or {}
                for k, v in seg.items():
                    try:
                        out[int(k)] = float(v.get("last_price") or 0.0)
                    except (ValueError, AttributeError):
                        out[int(k)] = 0.0
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < retries - 1:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise
        time.sleep(0.3)
    return out


def fetch_chain(symbol: str, expiry: str | None = None, token: str | None = None,
                client_id: str | None = None, window_pct: float = 8.0) -> dict | None:
    """Full chain around the money for one commodity expiry, via /optionchain.

    Returns {underlying, symbol, expiry, futures, futures_id, atm,
    rows:[{strike, option_type, security_id, ltp, bid, ask, iv, oi, volume}]}.
    """
    from parallax.adapters.env import env
    cid = client_id or env("DHAN_CLIENT_ID")
    tok = token or active_token()[0]
    expiry = expiry or nearest_expiry(symbol)
    if not expiry:
        return None
    fid = future_id(symbol, expiry)
    if not fid:
        return None
    body = json.dumps({"UnderlyingScrip": int(fid), "UnderlyingSeg": MCX_SEGMENT,
                       "Expiry": expiry}).encode()
    for attempt in range(3):
        req = urllib.request.Request(OPTIONCHAIN_URL, data=body,
                                     headers={"access-token": tok, "client-id": cid,
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = (json.loads(r.read().decode()) or {}).get("data") or {}
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 2:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise
    fprice = float(data.get("last_price") or 0.0)
    if not fprice:
        return None
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
                iv /= 100.0
            rows.append({
                "strike": strike, "option_type": otype.upper(),
                "security_id": leg.get("security_id"), "ltp": float(ltp),
                "oi": int(leg.get("oi") or 0), "volume": int(leg.get("volume") or 0),
                "iv": iv,
                "bid": float(leg.get("top_bid_price") or 0.0),
                "ask": float(leg.get("top_ask_price") or 0.0),
            })
    strikes = sorted(set(r["strike"] for r in rows))
    atm = min(strikes, key=lambda s: abs(s - fprice))
    lo = fprice * (1.0 - window_pct / 100.0)
    hi = fprice * (1.0 + window_pct / 100.0)
    near = [s for s in strikes if lo <= s <= hi]
    if len(near) > 120:
        near = sorted(sorted(strikes, key=lambda s: abs(s - fprice))[:120])
    if len(near) < 5:
        near = sorted(sorted(strikes, key=lambda s: abs(s - fprice))[:5])
    rows = [r for r in rows if r["strike"] in near]
    return {"underlying": spec(symbol).label, "symbol": spec(symbol).symbol,
            "expiry": expiry, "futures": fprice, "futures_id": fid,
            "atm": atm, "rows": rows}
