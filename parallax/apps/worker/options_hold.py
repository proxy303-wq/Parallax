"""Positional paper condor: enter once, hold to the contract expiry.

Unlike the 0DTE runner this does not manage the trade out.  A hedged condor
has a KNOWN worst case - the wings cap it at (width - credit) per unit - so
the position is carried to the expiry close and the whole credit is the
objective.  No ratchet, no stop.

Marks come from the Dhan WebSocket on the four legs, falling back to the REST
chain, so the value is real LTP rather than a model.

State lives in options_hold.json so a restart does not lose the position.

    python -m parallax.apps.worker.options_hold --enter     # open now
    python -m parallax.apps.worker.options_hold             # just manage
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

from parallax.adapters.broker.dhan import DhanBroker
from parallax.adapters.telegram import TelegramBot
from parallax.apps.worker.live_runner import IST

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
STATE = os.path.join(REPO, "options_hold.json")
#: Funded size.  Dhan's multi-leg calculator puts a 15-lot NIFTY condor at
#: Rs 19,41,121 and an 8-lot at Rs 10,35,264; on Rs 8L the ceiling is 6 lots
#: (Rs 7,76,448), so 5 leaves a margin buffer.  Exposure margin - ~94% of the
#: requirement - is NOT netted by the hedge, which is why these are so large.
LOTS = 5
POLL = 15
INSTRUMENT = "NIFTY 0DTE HOLD"     # distinct row, so it never collides with the runner


def _say(msg: str) -> None:
    print(msg, flush=True)
    try:
        tb = TelegramBot()
        if tb.configured:
            tb.send(msg)
    except Exception:
        pass


def arg(flag: str, default):
    """--flag value from argv, else default."""
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def load_state(path: str | None = None):
    try:
        with open(path or STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def save_state(plan: dict, path: str | None = None) -> None:
    with open(path or STATE, "w", encoding="utf-8") as fh:
        json.dump({"plan": plan, "lots": LOTS,
                   "opened": datetime.now(timezone.utc).isoformat()}, fh)


def clear_state(path: str | None = None) -> None:
    try:
        os.remove(path or STATE)
    except OSError:
        pass


def funds_report(ot, plan: dict, lots: int, dry: bool) -> None:
    """Exactly what this order needs, leg by leg, at the live prices.

    Dhan's margin calculator prices ONE leg and returns the NAKED figure, so it
    cannot answer what the basket costs.  It can, however, answer the two things
    that matter: how much cash the hedges take out, how much the shorts bring
    in, and what the two shorts would need if the hedge benefit is NOT applied.
    """
    UNITS = lots * 65
    api = None
    try:
        api = ot.broker._api_client()
    except Exception:
        api = None
    prem_out = prem_in = naked = 0.0
    _say("[FUNDS] leg-by-leg at live prices, %d lots (%d units each):" % (lots, UNITS))
    for name, l in plan["legs"].items():
        side = "SELL" if name.endswith("short") else "BUY"
        px = l["bid"] if side == "SELL" else l["ask"]
        cash = px * UNITS
        m = None
        if api is not None:
            try:
                res = api.margin_calculator(str(l["security_id"]), "NSE_FNO", side,
                                            int(UNITS), "INTRADAY", float(px), 0)
                d = (res or {}).get("data") or res or {}
                m = float(d.get("totalMargin") or 0.0)
            except Exception:
                m = None
        if side == "SELL":
            prem_in += cash
            if m:
                naked += m
        else:
            prem_out += cash
        _say("   %-11s %-4s %6.0f %-4s px %7.2f  cash %s Rs%-9s margin Rs%s" % (
            name, l["type"], l["strike"], side, px,
            "in " if side == "SELL" else "out", format(int(cash), ","),
            format(int(m), ",") if m is not None else "n/a"))
    credit = plan["credit"]
    ceiling = (100 - credit) * 65 * lots
    avail = 0.0
    try:
        avail = float(getattr(ot.broker.get_account(), "available", 0.0) or 0.0)
    except Exception:
        pass
    _say("[FUNDS] hedge premium out      Rs%s" % format(int(prem_out), ","))
    _say("[FUNDS] short premium in       Rs%s" % format(int(prem_in), ","))
    _say("[FUNDS] net credit received    Rs%s" % format(int(prem_in - prem_out), ","))
    _say("[FUNDS] peak cash needed       Rs%s  (the hedges go on first)" % (
        format(int(prem_out), ",")))
    _say("[FUNDS] defined-risk ceiling   Rs%s   <- max LOSS, not margin" % (
        format(int(ceiling), ",")))
    _say("[FUNDS] two naked shorts       Rs%s   <- single-leg calculator" % (
        format(int(naked), ",")))
    # The real number: Dhan's multi-leg calculator prices the whole basket.
    basket = []
    for name, l in plan["legs"].items():
        basket.append({
            "security_id": l["security_id"],
            "transaction_type": "SELL" if name.endswith("short") else "BUY",
            "quantity": UNITS,
            "price": l["bid"] if name.endswith("short") else l["ask"],
        })
    try:
        m = ot.broker.basket_margin(basket)
    except Exception as e:
        m = {"error": str(e)[:120]}
    if m.get("totalMargin") is not None:
        _say("[FUNDS] ACTUAL basket margin   Rs%s   <- what Dhan blocks" % (
            format(int(m["totalMargin"]), ",")))
        _say("[FUNDS]    span Rs%s + exposure Rs%s (hedgeBenefit Rs%s)" % (
            format(int(m.get("spanMargin") or 0), ","),
            format(int(m.get("exposure") or 0), ","),
            format(int(m.get("hedgeBenefit") or 0), ",")))
        if avail and m["totalMargin"] > avail:
            _say("[FUNDS] SHORT BY Rs%s on an %s account" % (
                format(int(m["totalMargin"] - avail), ","),
                format(int(avail), ",")))
    else:
        _say("[FUNDS] basket margin unavailable: " + str(m)[:120])
    _say("[FUNDS] account available      Rs%s%s" % (
        format(int(avail), ","),
        "   [PAPER: no margin is enforced, so this will proceed regardless]"
        if dry else ""))
    if not dry and avail and avail < prem_out:
        _say("[FUNDS] INSUFFICIENT for the hedges - need Rs%s" % (
            format(int(prem_out - avail), ",")))


def main() -> None:
    from parallax.apps.worker.dhan_options_live import ZeroDteCondor
    from parallax.web.store import JournalStore

    global LOTS, STATE, INSTRUMENT, POLL
    enter_now = "--enter" in sys.argv
    lots = int(arg("--lots", LOTS))
    LOTS = lots                     # save_state() records the global
    width = int(arg("--width", 2))
    STATE = arg("--state", STATE)
    INSTRUMENT = arg("--instrument", INSTRUMENT)
    POLL = int(arg("--poll", POLL))
    enter_at = arg("--enter-at", None)
    store = JournalStore()
    dry = store.mode() != "live"
    st = load_state()
    if st and st.get("plan"):
        # The POSITION's size beats the CLI default.  Restoring an 8-lot
        # position into a worker started with the new 5-lot default marked it
        # and reported its P&L at 5 lots - a 37% understatement of the truth.
        if st.get("lots"):
            lots = int(st["lots"])
            LOTS = lots
            _say("[HOLD] restoring %d lots from %s" % (lots, STATE))

    ot = ZeroDteCondor(broker=DhanBroker(dry_run=dry), lots=lots,
                       hold_to_expiry=True, instrument_name=INSTRUMENT)

    if st and st.get("plan"):
        ot.active = {"plan": st["plan"], "entry_time": datetime.now(timezone.utc)}
        ot.last_value = st["plan"]["credit"]
        ot.peak_pct = 0.0
        ot._start_feed(st["plan"])
        _say("[HOLD] restored position, expiry " + str(st["plan"].get("expiry")))
    elif enter_now:
        if enter_at:
            # wait for the requested clock time before pricing the trade
            _say("[HOLD] armed, entering at %s IST (%d lots, width %d)"
                 % (enter_at, lots, width))
            while True:
                if datetime.now(IST).strftime("%H:%M") >= enter_at:
                    break
                time.sleep(5)
        plan = ot.select(force=True, width=width)   # force: any day will do
        if not plan.get("legs"):
            _say("[HOLD] cannot enter: " + str(plan.get("reason")))
            return
        _say("[HOLD] entering %dL condor ATM%.0f credit %.2fpts iv %.1f%% rv %.1f%% expiry %s [%s]"
             % (lots, plan["atm"], plan["credit"], plan["iv"] * 100,
                plan["realized"] * 100, plan.get("expiry"),
                "PAPER" if dry else "LIVE"))
        funds_report(ot, plan, lots, dry)
        ot.enter(plan)
        if ot.active is None:
            _say("[HOLD] entry failed - see the acks above")
            return
        save_state(plan)
    else:
        _say("[HOLD] no position and no --enter flag; nothing to do")
        return

    credit = ot.active["plan"]["credit"]
    if not credit or credit <= 0:
        _say("[HOLD] refusing to manage a position with a non-positive credit")
        return
    exp = ot.active["plan"].get("expiry")
    maxp = credit * 65 * lots
    maxl = (100 - credit) * 65 * lots
    _say("[HOLD] %d lots  credit %.2f pts  max profit Rs%s  max loss Rs%s  expiry %s"
         % (lots, credit, format(int(maxp), ","), format(int(maxl), ","), exp))

    last_day = None
    while True:
        try:
            now = datetime.now(IST)
            val = ot.value_now()
            if val is not None:
                prof = (credit - val) / credit
                # the table has only (entry, stop, target).  entry = the credit
                # taken, stop = the live mark (cost to close), target = the open
                # P&L in rupees - the dashboard renders these three as
                # Entry / Mark / Open P&L for strategies that publish a mark.
                store.set_position(INSTRUMENT, "options-hold", "SELL", lots,
                                   credit, val, ot.last_pnl)
                if now.date() != last_day:
                    last_day = now.date()
                    # "profit 3.8%" read like a return on capital, which it is
                    # NOT: it is the fraction of the CREDIT captured.  Say so,
                    # and print the return on the margin alongside it.
                    _say("[HOLD] %s  value %.2f  pnl Rs%s  (%.1f%% of the "
                         "%.2f-pt credit, %.1f%% of the Rs%s margin)"
                         % (now.strftime("%a %d %b %H:%M"), val,
                            format(int(ot.last_pnl), ","), prof * 100, credit,
                            100.0 * ot.last_pnl / maxl, format(int(maxl), ",")))
            if ot.expiry_reached(now):
                _say("[HOLD] expiry reached - closing")
                ot.close("expiry")
                clear_state()
                _say("[HOLD] done. journal: " + str(store.summary()))
                return
        except Exception as e:
            _say("[HOLD] error: " + type(e).__name__ + " " + str(e)[:120])
        time.sleep(POLL)


if __name__ == "__main__":
    main()
