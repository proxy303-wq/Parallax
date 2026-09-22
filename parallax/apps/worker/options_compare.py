"""Side-by-side comparison of every held condor, marked from the live chain.

    python -m parallax.apps.worker.options_compare
"""
from __future__ import annotations

import glob
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
LOT = 65


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def main() -> None:
    from parallax.adapters.market_data.dhan_options import fetch_option_chain

    files = sorted(glob.glob(os.path.join(REPO, "options_hold*.json")))
    states = [(os.path.basename(f), load(f)) for f in files]
    states = [(n, s) for n, s in states if s and s.get("plan")]
    if not states:
        print("no held positions")
        return

    ch = fetch_option_chain("NIFTY")
    rows = ({(r["strike"], r["option_type"]): r for r in ch["rows"]}
            if ch else {})
    print("live chain: spot %.1f expiry %s rows %d" % (
        ch["spot"] if ch else 0, ch.get("expiry") if ch else "-", len(rows)))
    print()

    hdr = ("%-14s %5s %6s %6s %8s %8s %8s %9s %9s %9s" % (
        "state", "lots", "short", "wing", "credit", "close", "width%",
        "open P&L", "%credit", "%margin"))
    print(hdr)
    print("-" * len(hdr))
    for name, st in states:
        p = st["plan"]
        lots = st.get("lots") or 8
        credit = p["credit"]
        close_cost = 0.0
        ok = True
        for n, l in p["legs"].items():
            r = rows.get((l["strike"], l["type"]))
            if not r:
                ok = False
                continue
            if n.endswith("short"):
                close_cost += r["ask"]
            else:
                close_cost -= r["bid"]
        if not ok:
            close_cost = credit
        pnl = (credit - close_cost) * LOT * lots
        margin = (100 - credit) * LOT * lots
        short_leg = [l for k, l in p["legs"].items() if k == "put_short"][0]
        wing = abs(short_leg["strike"] -
                   [l for k, l in p["legs"].items() if k == "put_hedge"][0]["strike"])
        print("%-14s %5d %6.0f %6.0f %8.2f %8.2f %7.1f%% %9s %8.1f%% %8.1f%%" % (
            name.replace("options_hold", "hold").replace(".json", ""), lots,
            short_leg["strike"], wing, credit, close_cost,
            100.0 * credit / wing, format(int(pnl), ","),
            100.0 * (credit - close_cost) / credit, 100.0 * pnl / margin))

    print()
    tot_pnl = 0.0
    tot_margin = 0.0
    for name, st in states:
        p = st["plan"]
        lots = st.get("lots") or 8
        credit = p["credit"]
        cc = 0.0
        for n, l in p["legs"].items():
            r = rows.get((l["strike"], l["type"]))
            if r:
                cc += r["ask"] if n.endswith("short") else -r["bid"]
        tot_pnl += (credit - cc) * LOT * lots
        tot_margin += (100 - credit) * LOT * lots
    print("combined open P&L Rs%s   combined max loss Rs%s   (%.1f%% of an 8L book)" % (
        format(int(tot_pnl), ","), format(int(tot_margin), ","),
        100.0 * tot_margin / 800000.0))


if __name__ == "__main__":
    main()
