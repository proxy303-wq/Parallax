"""Assemble an MCX commodity option chain from the scrip master + LTP feed.

Commodity options have no index-style /optionchain endpoint, so the chain is
built in two steps:

  1. scrip master  -> the strike grid and security ids for one expiry
  2. /marketfeed/ltp -> last-traded price per security id, batched

The "spot" for a commodity is the FUTURES price.  Commodity options expire a
few days BEFORE the futures they settle into (e.g. crude option 15th, future
19th), so the underlying future is the one whose expiry is the nearest date
on or after the option's expiry - not an exact-date match.

Only the strikes near the money are fetched: a full commodity chain is over a
thousand contracts and blows through the marketfeed rate limit, while a
condor only ever needs a couple of dozen strikes around the ATM.
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
    """Read one commodity's contracts once.

    Returns (futures, options) where futures = {expiry: id} and
    options = {expiry: {strike: {"CE": id, "PE": id}}}.
    """
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


def contracts(symbol: str, expiry: str) -> tuple[dict, int | None]:
    """({strike: {"CE": id, "PE": id}}, futures_id) for one option expiry."""
    futures, options = _load_symbol(symbol)
    grid = options.get(expiry, {})
    later = sorted(e for e in futures if e >= expiry)
    fut_id = futures[later[0]] if later else (futures[sorted(futures)[-1]] if futures else None)
    return grid, fut_id


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
    """Chain around the money for one commodity expiry.

    Returns {underlying, symbol, expiry, futures, futures_id, atm,
    rows:[{strike, option_type, security_id, ltp}]} or None.
    """
    from parallax.adapters.env import env
    cid = client_id or env("DHAN_CLIENT_ID")
    tok = token or active_token()[0]
    expiry = expiry or nearest_expiry(symbol)
    if not expiry:
        return None
    grid, fut_id = contracts(symbol, expiry)
    if not grid or not fut_id:
        return None

    fprice = fetch_ltp([fut_id], tok, cid).get(fut_id, 0.0)
    if not fprice:
        return None

    strikes = sorted(grid)
    atm = min(strikes, key=lambda s: abs(s - fprice))
    lo = fprice * (1.0 - window_pct / 100.0)
    hi = fprice * (1.0 + window_pct / 100.0)
    near = [s for s in strikes if lo <= s <= hi]
    if len(near) > 120:
        near = sorted(sorted(strikes, key=lambda s: abs(s - fprice))[:120])
    if len(near) < 5:
        near = sorted(sorted(strikes, key=lambda s: abs(s - fprice))[:5])

    ids = [fut_id]
    for s in near:
        for otype in ("CE", "PE"):
            sid = grid[s].get(otype)
            if sid:
                ids.append(sid)
    prices = fetch_ltp(ids, tok, cid)

    rows = []
    for strike in near:
        for otype in ("CE", "PE"):
            sid = grid[strike].get(otype)
            if sid:
                rows.append({"strike": strike, "option_type": otype,
                             "security_id": sid, "ltp": prices.get(sid, 0.0)})
    return {"underlying": spec(symbol).label, "symbol": spec(symbol).symbol,
            "expiry": expiry, "futures": fprice, "futures_id": fut_id,
            "atm": atm, "rows": rows}
