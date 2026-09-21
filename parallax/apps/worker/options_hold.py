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
LOTS = 8
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


def load_state():
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def save_state(plan: dict) -> None:
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump({"plan": plan, "opened": datetime.now(timezone.utc).isoformat()}, fh)


def clear_state() -> None:
    try:
        os.remove(STATE)
    except OSError:
        pass


def main() -> None:
    from parallax.apps.worker.dhan_options_live import ZeroDteCondor
    from parallax.web.store import JournalStore

    enter_now = "--enter" in sys.argv
    store = JournalStore()
    dry = store.mode() != "live"
    ot = ZeroDteCondor(broker=DhanBroker(dry_run=dry), lots=LOTS,
                       hold_to_expiry=True, instrument_name=INSTRUMENT)

    st = load_state()
    if st and st.get("plan"):
        ot.active = {"plan": st["plan"], "entry_time": datetime.now(timezone.utc)}
        ot.last_value = st["plan"]["credit"]
        ot.peak_pct = 0.0
        ot._start_feed(st["plan"])
        _say("[HOLD] restored position, expiry " + str(st["plan"].get("expiry")))
    elif enter_now:
        plan = ot.select(force=True)      # force: today is not an expiry day
        if not plan.get("legs"):
            _say("[HOLD] cannot enter: " + str(plan.get("reason")))
            return
        _say("[HOLD] entering %dL condor ATM%.0f credit %.2fpts iv %.1f%% rv %.1f%% expiry %s [%s]"
             % (LOTS, plan["atm"], plan["credit"], plan["iv"] * 100,
                plan["realized"] * 100, plan.get("expiry"),
                "PAPER" if dry else "LIVE"))
        ot.enter(plan)
        if ot.active is None:
            _say("[HOLD] entry failed - see the acks above")
            return
        save_state(plan)
    else:
        _say("[HOLD] no position and no --enter flag; nothing to do")
        return

    credit = ot.active["plan"]["credit"]
    exp = ot.active["plan"].get("expiry")
    maxp = credit * 65 * LOTS
    maxl = (100 - credit) * 65 * LOTS
    _say("[HOLD] %d lots  credit %.2f pts  max profit Rs%s  max loss Rs%s  expiry %s"
         % (LOTS, credit, format(int(maxp), ","), format(int(maxl), ","), exp))

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
                store.set_position(INSTRUMENT, "options-hold", "SELL", LOTS,
                                   credit, val, ot.last_pnl)
                if now.date() != last_day:
                    last_day = now.date()
                    _say("[HOLD] %s  value %.2f  profit %+.1f%%  pnl Rs%s"
                         % (now.strftime("%a %d %b %H:%M"), val, prof * 100,
                            format(int(ot.last_pnl), ",")))
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
