"""Commodity 0DTE iron condor worker - PAPER ONLY.

Fires at 3:45 PM IST on a commodity option expiry day (Gold Mini / Crude Mini),
papers the 9/2 condor against a SEPARATE Rs10L balance, holds to the 23:30 IST
commodity close, settles at the futures close, and journals to its own store.

There is no live path.  The index book settles at 15:30 and commodities close
at 23:30, so this worker is structurally disjoint from the index worker.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from parallax.adapters.market_data.commodity_chain import fetch_chain, list_expiries
from parallax.apps.ops.commodity_condor import condor_from_chain
from parallax.config.commodities import BOOK, book_lots, spec

IST = timezone(timedelta(hours=5, minutes=30))
SETTLE_HHMM = (23, 30)

COMMODITY_DB = os.environ.get("PARALLAX_COMMODITY_DB", "/opt/parallax/commodity.db")
PAPER_BALANCE = float(BOOK["paper_balance"])


def now_ist():
    return datetime.now(IST)


def commodity_plan(today=None):
    """Commodity whose option expires on today, or None."""
    today = today or now_ist().date().isoformat()
    for sym in BOOK["lots"]:
        if today in list_expiries(sym):
            return sym
    return None


def condor_payoff(sp, lp, sc, lc, settle):
    """Condor intrinsic per unit at settlement (negative = loss), credit excluded."""
    v = 0.0
    if settle < sp:
        v -= sp - settle
    if settle < lp:
        v += lp - settle
    if settle > sc:
        v -= settle - sc
    if settle > lc:
        v += settle - lc
    return v


def _store():
    from parallax.web.store import JournalStore
    s = JournalStore(COMMODITY_DB)
    if s.paper_capital() != PAPER_BALANCE:
        s.set_paper_capital(PAPER_BALANCE)
    return s


def run_session(symbol, say=print, settle_hhmm=SETTLE_HHMM, fetch=fetch_chain, now=False):
    """Paper one condor: enter now, settle at the commodity close."""
    store = _store()
    ch = fetch(symbol)
    if not ch:
        say("PROBE commodity chain unavailable for " + symbol)
        return None
    c = condor_from_chain(ch, BOOK["short_off"], BOOK["wing"])
    if not c:
        say("PROBE condor legs missing for " + symbol)
        return None
    lots = book_lots(symbol)
    lot = spec(symbol).lot or 1
    units = lots * lot
    tag = symbol + "-" + ch["expiry"]
    say("ENTER " + symbol + " " + c["shape"] + "  " + str(lots) + " lots  credit "
        + str(c["credit"]) + " pts  max loss Rs." + format(int(c["max_loss"] * units), ","))
    store.set_position(instrument=tag, strategy="commodity 0DTE condor", side="SHORT",
                       qty=lots, entry=c["credit"], stop=c["max_loss"], target=c["credit"])

    if not now:
        target = now_ist().replace(hour=settle_hhmm[0], minute=settle_hhmm[1],
                                   second=0, microsecond=0)
        while now_ist() < target:
            time.sleep(30)

    ch2 = fetch(symbol)
    if not ch2:
        say("PROBE settlement chain unavailable")
        return None
    settle = ch2["futures"]
    iv = condor_payoff(c["short_put"], c["long_put"], c["short_call"], c["long_call"], settle)
    pnl = (c["credit"] + iv) * units
    outcome = "WIN" if pnl >= 0 else "LOSS"
    say("SETTLE " + symbol + "  settle " + str(round(settle, 1)) + "  intrinsic "
        + str(round(iv, 2)) + "  P&L Rs." + format(int(round(pnl)), ",") + "  " + outcome)
    store.record_trade(strategy="commodity 0DTE condor", instrument=tag, side="SHORT",
                       qty=lots, entry=c["credit"], exit_price=settle, pnl=pnl,
                       outcome=outcome, note=symbol + " " + c["shape"])
    store.clear_position(tag)
    return pnl


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", action="store_true", help="enter and settle immediately (test)")
    ap.add_argument("--symbol", default="", help="override the planned symbol")
    args = ap.parse_args()
    symbol = args.symbol or commodity_plan()
    if not symbol:
        print("no commodity option expires today - idle")
        return 0
    run_session(symbol, now=args.now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
