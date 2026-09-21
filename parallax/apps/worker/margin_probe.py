"""Margin probe: does the Dhan API apply the hedge benefit to a hedged leg?

This is the one open question standing between the 0DTE condor and a funded
account, and it cannot be answered from documentation.  Dhan has no basket
order API (no such method in dhanhq; MadeForTrade #59802 is an open feature
request for it), so the runner places legs as separate orders, hedges first.
Whether the short leg that follows is then margined as a SPREAD or as a
NAKED short decides everything:

    netted    -> 8 lots need ~Rs 40,000   and an Rs 8L account is fine
    not netted-> each lot needs ~Rs 3.28 lakh of naked-short margin
                 (queried: Rs 13.13L + Rs 13.09L per 8 lots), so an Rs 8L
                 account caps out at 2 lots

Forum reports disagree.  "Fire your hedge leg first... this way u will get
hedge benefit after order execution" (MadeForTrade #42869) is contradicted in
the same thread by "no hedge benefit, it is considering them to be completely
separate orders", and a second thread reports a SELL cancelled for insufficient
balance after its hedge was already filled (#56059).

So measure it.  This script buys one lot of the far put, then sells one lot of
the near put, and reads the margin actually blocked between the two steps.
It then flattens both legs.

    python -m parallax.apps.worker.margin_probe            # dry run, no orders
    python -m parallax.apps.worker.margin_probe --live     # places real orders

Cost of the live run: two round trips of one lot plus the spread.  The position
is opened and closed within seconds and carries no overnight risk.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from parallax.adapters.broker.dhan import DhanBroker
from parallax.adapters.market_data.dhan_options import fetch_option_chain
from parallax.core.options.contracts import OptionContract

LOT = 65
STEP = 50.0


def _limits(b: DhanBroker) -> dict:
    try:
        f = b._api_client().get_fund_limits().get("data") or {}
    except Exception:
        f = {}
    def g(*k):
        for kk in k:
            try:
                return float(f.get(kk) or 0.0)
            except (TypeError, ValueError):
                continue
        return 0.0
    return {"available": g("availabelBalance", "availableBalance"),
            "utilized": g("utilizedAmount", "amountUtilized"),
            "collateral": g("collateralAmount"),
            "raw": f}


def _contract(leg) -> OptionContract:
    return OptionContract(symbol="NIFTY", strike=leg["strike"], expiry="",
                          option_type=leg["type"], lot_size=LOT,
                          security_id=leg["security_id"], trading_symbol="")


def main() -> None:
    live = "--live" in sys.argv
    b = DhanBroker(dry_run=not live)
    if not b.configured:
        print("Dhan not configured")
        return
    chain = fetch_option_chain("NIFTY")
    if not chain:
        print("option chain unavailable (market closed?)")
        return
    spot = float(chain["spot"])
    atm = round(spot / STEP) * STEP
    rows = {(r["strike"], r["option_type"]): r for r in chain["rows"]}
    far = rows.get((atm - 4 * STEP, "PE"))
    near = rows.get((atm - 2 * STEP, "PE"))
    if not far or not near:
        print("missing strikes")
        return
    print("NIFTY %.1f  ATM %.0f   mode=%s" % (spot, atm, "LIVE" if live else "dry"))
    print("  hedge  BUY  %6.0f PE  id=%s  ask=%.2f" % (
        far["strike"], far["security_id"], far["ask"]))
    print("  short  SELL %6.0f PE  id=%s  bid=%.2f" % (
        near["strike"], near["security_id"], near["bid"]))
    spread_pts = near["strike"] - far["strike"]
    print("  spread width %d pts -> netted margin ~Rs %s per lot" % (
        spread_pts, format(int(spread_pts * LOT), ",")))

    before = _limits(b)
    print("\nbefore      available Rs %s  utilized Rs %s" % (
        format(int(before["available"]), ","), format(int(before["utilized"]), ",")))
    if not live:
        print("\ndry run - no orders placed.  Re-run with --live once funded.")
        return

    acks = []
    for label, leg, side in (("hedge", far, "BUY"), ("short", near, "SELL")):
        ack = b.place_option_order(_contract(leg), side, 1, "MARKET")
        acks.append((leg, side))
        print("%-11s %-4s -> %s %s" % (label, side,
                                       getattr(ack, "status", "?"),
                                       str(getattr(ack, "message", ""))[:60]))
        if "REJECT" in str(getattr(ack, "status", "")).upper():
            print("  REJECTED - unwinding")
            for l2, s2 in reversed(acks[:-1]):
                b.place_option_order(_contract(l2),
                                     "SELL" if s2 == "BUY" else "BUY", 1, "MARKET")
            return
        cur = _limits(b)
        print("            available Rs %s  utilized Rs %s  (blocked Rs %s)" % (
            format(int(cur["available"]), ","), format(int(cur["utilized"]), ","),
            format(int(cur["utilized"] - before["utilized"]), ",")))

    after = _limits(b)
    blocked = after["utilized"] - before["utilized"]
    netted_ref = spread_pts * LOT          # 1 lot of a 100-pt spread
    naked_ref = 164_000.0                  # queried: Rs 13.13L per 8 lots
    print("\nblocked total Rs %s" % format(int(blocked), ","))
    print("  reference: netted spread would block ~Rs %s, naked ~Rs %s" % (
        format(int(netted_ref), ","), format(int(naked_ref), ",")))
    if blocked < (netted_ref + naked_ref) / 2.0:
        print("VERDICT: hedge benefit APPLIED - the short was margined as a")
        print("         spread, so 8 lots of the condor fit an Rs 8L account.")
    else:
        print("VERDICT: NO hedge benefit - the short was margined naked.")
        print("         An Rs 8L account caps the condor at ~2 lots.")

    print("\nflattening ...")
    for l2, s2 in reversed(acks):
        ack = b.place_option_order(_contract(l2),
                                   "SELL" if s2 == "BUY" else "BUY", 1, "MARKET")
        print("  %-4s %6.0f -> %s" % ("SELL" if s2 == "BUY" else "BUY",
                                       l2["strike"], getattr(ack, "status", "?")))


if __name__ == "__main__":
    main()
