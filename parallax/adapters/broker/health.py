"""Read-only connectivity probes for the wired broker adapters.

Each probe instantiates the broker in dry_run mode (orders are no-ops) and
fetches account / positions / contract resolution.  No order is ever placed.
Values are returned, not logged; callers decide what to surface (mask secrets).
"""
from __future__ import annotations

from parallax.adapters.env import env, mask
from parallax.adapters.broker.delta import DeltaBroker
from parallax.adapters.broker.dhan import DhanBroker


def probe_dhan(instrument: str = "NIFTY") -> dict:
    out: dict = {"broker": "dhan", "configured": False, "errors": []}
    b = DhanBroker(dry_run=True, instrument=instrument)
    out["configured"] = b.configured
    out["client_id"] = mask(b.client_id)
    out["token_source"] = b.token_source
    out["auth_error"] = b._auth_error
    if not b.configured:
        out["errors"].append("DHAN_CLIENT_ID/ACCESS_TOKEN missing")
        return out
    try:
        sid, tsym, expiry, lot = b.resolve_contract()
        out["contract"] = {"security_id": sid, "trading_symbol": tsym,
                           "expiry": expiry, "lot": lot}
    except Exception as e:
        out["contract"] = None
        out["errors"].append(f"contract: {e}")
    try:
        acct = b.get_account()
        out["account"] = {"cash": round(acct.cash, 2), "currency": acct.currency}
    except Exception as e:
        out["account"] = None
        out["errors"].append(f"account: {type(e).__name__} {str(e)[:160]}")
    try:
        pos = b.get_positions()
        out["positions"] = [
            {"symbol": p.instrument, "side": p.side.value, "qty": p.quantity,
             "entry": round(p.avg_entry, 2), "upnl": round(p.unrealized_pnl, 2)}
            for p in pos]
    except Exception as e:
        out["positions"] = None
        out["errors"].append(f"positions: {type(e).__name__} {str(e)[:160]}")
    return out


def probe_delta() -> dict:
    out: dict = {"broker": "delta", "configured": False, "errors": []}
    b = DeltaBroker(dry_run=True)
    out["configured"] = b.configured
    out["api_key"] = mask(b.api_key)
    if not b.configured:
        out["errors"].append("DELTA_API_KEY/SECRET missing")
        return out
    try:
        acct = b.get_account()
        out["account"] = {"cash": round(acct.cash, 2), "currency": acct.currency}
    except Exception as e:
        out["account"] = None
        out["errors"].append(f"account: {type(e).__name__} {str(e)[:160]}")
    try:
        pos = b.get_positions()
        out["positions"] = [
            {"symbol": p.instrument, "side": p.side.value, "qty": p.quantity,
             "entry": round(p.avg_entry, 4), "upnl": round(p.unrealized_pnl, 4)}
            for p in pos]
    except Exception as e:
        out["positions"] = None
        out["errors"].append(f"positions: {type(e).__name__} {str(e)[:160]}")
    try:
        q = b.get_quote("DELTA:BTC")
        out["quote_BTC"] = {"bid": round(q.bid, 2), "ask": round(q.ask, 2),
                            "last": round(q.last, 2)}
    except Exception as e:
        out["quote_BTC"] = None
        out["errors"].append(f"quote: {type(e).__name__} {str(e)[:160]}")
    return out


def probe_all() -> dict:
    return {"dhan": probe_dhan(), "delta": probe_delta()}
