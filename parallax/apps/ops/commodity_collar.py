"""Collar hedge on MCX commodity futures.

Long futures + long put + short call = bounded both ways.  The put floor and
the call cap are picked as a percentage off the futures price and snapped to
the real strike grid from the chain fetcher.

Payoff per unit, futures entered at F, put Kp costing p, call Kc earning c:

    net  = p - c                       (debit if positive, credit if "free")
    S <= Kp : floor = Kp - F - net     (downside floored by the put)
    S >= Kc : cap   = Kc - F - net     (upside capped by the call)

A cost-free (or credit) collar is the case net <= 0: the call finances the put.
"""
from __future__ import annotations

from parallax.adapters.market_data.commodity_chain import fetch_chain


def collar_strikes(futures: float, strikes: list[float], put_pct: float,
                   call_pct: float) -> tuple[float | None, float | None]:
    """Nearest available put below / call above the futures price."""
    put_target = futures * (1.0 - put_pct / 100.0)
    call_target = futures * (1.0 + call_pct / 100.0)
    below = [s for s in strikes if s <= put_target]
    above = [s for s in strikes if s >= call_target]
    return (below[-1] if below else None, above[0] if above else None)


def payoff(futures: float, put_strike: float, call_strike: float, net: float,
           settle: float) -> float:
    """Collar P&L per unit at a settlement price."""
    return (settle - futures) + max(put_strike - settle, 0.0) - max(settle - call_strike, 0.0) - net


def collar_from_chain(ch: dict, put_pct: float = 5.0, call_pct: float = 5.0) -> dict | None:
    """Collar from an already-fetched chain dict."""
    f = ch["futures"]
    by_strike: dict[float, dict] = {}
    for r in ch["rows"]:
        by_strike.setdefault(r["strike"], {})[r["option_type"]] = r
    strikes = sorted(by_strike)
    put_strike, call_strike = collar_strikes(f, strikes, put_pct, call_pct)
    if put_strike is None or call_strike is None:
        return None
    put_leg = by_strike[put_strike].get("PE")
    call_leg = by_strike[call_strike].get("CE")
    if not put_leg or not call_leg:
        return None
    p, c = put_leg["ltp"], call_leg["ltp"]
    net = p - c
    return {
        "symbol": ch.get("symbol"), "underlying": ch.get("underlying"),
        "expiry": ch.get("expiry"),
        "futures": f, "futures_id": ch.get("futures_id"),
        "put_strike": put_strike, "put_id": put_leg["security_id"], "put_ltp": p,
        "call_strike": call_strike, "call_id": call_leg["security_id"], "call_ltp": c,
        "net": net,
        "floor": put_strike - f - net,
        "cap": call_strike - f - net,
        "floor_level": put_strike, "cap_level": call_strike,
    }


def collar(symbol: str, put_pct: float = 5.0, call_pct: float = 5.0,
           expiry: str | None = None, token: str | None = None,
           client_id: str | None = None) -> dict | None:
    """Full collar for one commodity, priced off the live chain."""
    ch = fetch_chain(symbol, expiry=expiry, token=token, client_id=client_id)
    if not ch:
        return None
    return collar_from_chain(ch, put_pct, call_pct)
