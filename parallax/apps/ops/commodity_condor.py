"""Iron condor on MCX commodity futures options.

Same shape as the index book - short strikes at the ATM +/- short_off steps,
hedges wing steps further out - but priced off the commodity chain, where the
"spot" is the futures price and there is no index-style chain endpoint.
"""
from __future__ import annotations

from parallax.adapters.market_data.commodity_chain import fetch_chain
from parallax.config.commodities import spec


def condor_from_chain(ch: dict, short_off: int, wing: int) -> dict | None:
    """Price one condor off an already-fetched chain."""
    f = ch["futures"]
    step = spec(ch["symbol"]).step
    atm = round(f / step) * step
    sc = atm + short_off * step
    sp = atm - short_off * step
    lc = sc + wing * step
    lp = sp - wing * step
    legs = {(r["strike"], r["option_type"]): r for r in ch["rows"]}
    sc_r, sp_r = legs.get((sc, "CE")), legs.get((sp, "PE"))
    lc_r, lp_r = legs.get((lc, "CE")), legs.get((lp, "PE"))
    if not all((sc_r, sp_r, lc_r, lp_r)):
        return None
    credit = sc_r["ltp"] + sp_r["ltp"] - lc_r["ltp"] - lp_r["ltp"]
    width = wing * step
    lot = spec(ch["symbol"]).lot or 1
    return {
        "symbol": ch["symbol"], "underlying": ch.get("underlying"),
        "expiry": ch["expiry"], "futures": f, "atm": atm,
        "shape": "%d/%d" % (short_off, wing),
        "short_off": short_off, "wing": wing,
        "short_call": sc, "short_put": sp, "long_call": lc, "long_put": lp,
        "credit": round(credit, 2),
        "max_profit": round(credit, 2),
        "max_loss": round(width - credit, 2),
        "window": short_off * step, "width": width, "lot": lot,
        "max_profit_rs": round(credit * lot, 2),
        "max_loss_rs": round((width - credit) * lot, 2),
    }


def condor(symbol: str, short_off: int, wing: int, expiry: str | None = None,
           token: str | None = None, client_id: str | None = None) -> dict | None:
    ch = fetch_chain(symbol, expiry=expiry, token=token, client_id=client_id)
    if not ch:
        return None
    return condor_from_chain(ch, short_off, wing)
