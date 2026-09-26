"""Positional paper condor: enter once, hold to the contract expiry.

Unlike the 0DTE runner this does not manage the trade out.  A hedged condor
has a KNOWN worst case - the wings cap it at (width - credit) per unit - so
the position is carried to the expiry close and the whole credit is the
objective.  No ratchet, no stop.

Marks come from the Dhan WebSocket on the four legs, falling back to the REST
chain, so the value is real LTP rather than a model.

State lives in options_hold_<INDEX>.json so a restart does not lose the
position.

    python -m parallax.apps.worker.options_hold --enter     # open now
    python -m parallax.apps.worker.options_hold             # just manage

The CLI is a thin wrapper over run_session(); options_supervisor.py calls the
same function, because the SCHEDULE - not the process - decides which index has
work on a given day.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from parallax.adapters.broker.dhan import DhanBroker
from parallax.adapters.telegram import TelegramBot
from parallax.apps.worker.live_runner import IST

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
STATE = os.path.join(REPO, "options_hold.json")
#: Funded size.
#:
#: The figures that used to sit here (15 lots Rs 19,41,121, 8 lots Rs 10,35,264,
#: i.e. Rs 1,29,408/lot) are ~2x what Dhan actually returns now and are not
#: reproducible at any shape or size I measured.
#:
#: Measured 2026-09-25 against /margincalculator/multi at short_off 5 / wing 3:
#: Rs 70,369 per lot for NIFTY, and it is exactly linear - 4, 8 and 10 lots all
#: came back to the rupee.  So 7 lots is Rs 4,92,584, which is 62% of the Rs 8L
#: paper book.
#:
#: The reason the requirement is large is that only SPAN nets against the hedge.
#: Exposure - ~85% of the total - is charged on the shorts whatever the wings do.
#: Hedging collapses SPAN 13.8x (Rs 12,54,383 -> Rs 90,779 at 10 lots) and moves
#: exposure not at all (Rs 6,00,823 either way).
LOTS = 7
POLL = 15
# There was an INSTRUMENT = "NIFTY 0DTE HOLD" constant here, claiming a name
# that never collides with the runner.  It was dead: main() defaults to
# "%s 0DTE" % index, which is the SAME key the runner's condor used, so the two
# engines upserted over each other.  The runner now writes "NIFTY 0DTE
# INTRADAY" and the entry guard below keeps them from running together.


def _say(msg: str) -> None:
    print(msg, flush=True)
    try:
        tb = TelegramBot()
        if tb.configured:
            tb.send(msg)
    except Exception:
        pass


def _idle() -> None:
    """Stay alive without touching the API, so systemd does not restart-loop."""
    while True:
        time.sleep(300)


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


def save_state(plan: dict, path: str | None = None,
               lots: int | None = None) -> None:
    with open(path or STATE, "w", encoding="utf-8") as fh:
        json.dump({"plan": plan, "lots": LOTS if lots is None else lots,
                   "opened": datetime.now(timezone.utc).isoformat()}, fh)


def clear_state(path: str | None = None) -> None:
    try:
        os.remove(path or STATE)
    except OSError:
        pass


def funds_report(ot, plan: dict, lots: int, dry: bool, wing: int) -> None:
    """Exactly what this order needs, leg by leg, at the live prices.

    Dhan's margin calculator prices ONE leg and returns the NAKED figure, so it
    cannot answer what the basket costs.  It can, however, answer the two things
    that matter: how much cash the hedges take out, how much the shorts bring
    in, and what the two shorts would need if the hedge benefit is NOT applied.
    """
    UNITS = lots * ot.lot        # per-index lot, not NIFTY's 65
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
    ceiling = (wing * ot.step - credit) * ot.lot * lots    # wing is per-index
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


def _ist_now() -> datetime:
    """Now, in IST.  Never a naive local read: the box may well be on UTC."""
    return datetime.now(IST)


def run_session(index: str = "NIFTY", lots: int = LOTS, short_off: int = 3,
                wing: int = 3, state: str | None = None,
                instrument: str | None = None, poll: int = POLL,
                enter_at: str | None = None, enter_now: bool = False,
                deadline: datetime | None = None, plan_fn=None, now_fn=None,
                sleep=time.sleep) -> str:
    """Hold one index's positional condor for one session.

    Split out of main() so a schedule-driven supervisor can run the one index
    that is due from a single process.  Returns a short outcome - "expired",
    "entry-failed", "deadline", "no-credit" or "no-position" - for the caller
    to log; the CLI is a thin wrapper and behaves exactly as it did before.

    deadline bounds the WAIT FOR THE ENTRY WINDOW only.  A position that is
    already open is always managed to its expiry: standing down while real risk
    is on the book would be the worst of both worlds.  The CLI passes None,
    which is the old behaviour - wait for this index's next due day.
    """
    from parallax.apps.worker.dhan_options_live import ZeroDteCondor
    from parallax.config.schedule import options_plan as _plan
    from parallax.web.store import JournalStore

    now_fn = now_fn or _ist_now
    plan_fn = plan_fn or _plan
    index = index.upper()
    state = state or os.path.join(REPO, "options_hold_%s.json" % index)
    instrument = instrument or ("%s 0DTE" % index)
    store = JournalStore()
    dry = store.mode() != "live"
    st = load_state(state)
    if st and st.get("plan"):
        # The POSITION's size beats the CLI default.  Restoring an 8-lot
        # position into a worker started with the new 5-lot default marked it
        # and reported its P&L at 5 lots - a 37% understatement of the truth.
        if st.get("lots"):
            lots = int(st["lots"])
            _say("[HOLD] restoring %d lots from %s" % (lots, state))

    ot = ZeroDteCondor(broker=DhanBroker(dry_run=dry), lots=lots,
                       hold_to_expiry=True, instrument_name=instrument,
                       index=index)

    if st and st.get("plan"):
        ot.active = {"plan": st["plan"], "entry_time": datetime.now(timezone.utc)}
        ot.last_value = st["plan"]["credit"]
        ot.peak_pct = 0.0
        ot._start_feed(st["plan"])
        _say("[HOLD] restored position, expiry " + str(st["plan"].get("expiry")))
    elif enter_now:
        # Wait for a day this index is due, then for the entry window.  Both
        # checks have to live in the loop: a worker that idled once and never
        # looked again would sleep straight through its own expiry day.
        #
        #   last Tuesday  -> BANKNIFTY only (NIFTY and FINNIFTY skipped)
        #   last Thursday -> BANKEX only (SENSEX skipped)
        #   other Tue/Thu -> NIFTY / SENSEX
        hh = mm = None
        if enter_at:
            hh, mm = int(enter_at[:2]), int(enter_at[3:5])
        last_state = None
        while True:
            now = now_fn()
            # the supervisor bounds this wait to its own session, so a holder
            # started after the window has gone cannot sit here into tomorrow
            # and miss a different index that is actually due
            if deadline is not None and now >= deadline:
                _say("[%s] no entry window before %s IST - standing down"
                     % (index, deadline.strftime("%H:%M")))
                return "deadline"
            plan = plan_fn(now)
            due = index in plan
            in_window = True
            if due and hh is not None:
                target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                in_window = target <= now <= target + timedelta(minutes=30)
            mark = (due, in_window)
            if mark != last_state:
                last_state = mark
                if not due:
                    _say("[%s] not on today's plan %s - waiting"
                         % (index, plan if plan else "futures day"))
                else:
                    _say("[%s] due today, entering at %s IST (%d lots, %d-%d)"
                         % (index, enter_at or "now", lots, short_off, wing))
            if due and in_window:
                break
            sleep(60)

        # Mutual exclusion with live_runner's intraday condor: both engines
        # sell a NIFTY 0DTE condor on the same expiry day.  The runner already
        # defers to any open options row; this is the mirror of that check, so
        # whichever engine starts second stands down instead of stacking a
        # second condor on the same market.  Our own row is excluded by
        # instrument, since position rows are keyed on it.
        positions_fn = getattr(store, "positions", None)
        foreign = [p for p in (positions_fn() if positions_fn else [])
                   if str(p.get("strategy") or "").startswith("options")
                   and str(p.get("instrument") or "") != instrument]
        if foreign:
            _say("[HOLD] not entering - another options position is open: "
                 + ", ".join("%s/%s" % (p.get("instrument"), p.get("strategy"))
                             for p in foreign))
            return "position-held"

        plan = ot.select(force=True, short_off=short_off, wing=wing)
        if not plan.get("legs"):
            _say("[HOLD] cannot enter: " + str(plan.get("reason")))
            return "entry-failed"
        _say("[HOLD] entering %dL condor ATM%.0f credit %.2fpts iv %.1f%% rv %.1f%% expiry %s [%s]"
             % (lots, plan["atm"], plan["credit"], plan["iv"] * 100,
                plan["realized"] * 100, plan.get("expiry"),
                "PAPER" if dry else "LIVE"))
        funds_report(ot, plan, lots, dry, wing)
        ot.enter(plan)
        if ot.active is None:
            _say("[HOLD] entry failed - see the acks above")
            return "entry-failed"
        save_state(plan, state, lots)
    else:
        _say("[HOLD] no position and no --enter flag; nothing to do")
        return "no-position"

    credit = ot.active["plan"]["credit"]
    if not credit or credit <= 0:
        _say("[HOLD] refusing to manage a position with a non-positive credit")
        return "no-credit"
    exp = ot.active["plan"].get("expiry")
    # lot and wing are per-index.  Hardcoding 65 and 100 is right for NIFTY and
    # wrong by 3x for SENSEX (lot 20) and 3x on the wing for any 3-3 shape -
    # it printed "max loss Rs-13,701" for a position whose real worst case was
    # a Rs27,784 LOSS.
    maxp = credit * ot.lot * lots
    maxl = (wing * ot.step - credit) * ot.lot * lots
    _say("[HOLD] %d lots  credit %.2f pts  max profit Rs%s  max loss Rs%s  expiry %s"
         % (lots, credit, format(int(maxp), ","), format(int(maxl), ","), exp))

    last_day = None
    while True:
        try:
            now = now_fn()
            # Do not mark outside the session.  The feed keeps answering after
            # the close, and a stale or zero book is not a price.
            if not (now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) <= (15, 30)):
                if ot.expiry_reached(now):
                    pass          # fall through: the expiry close still has to run
                else:
                    sleep(poll)
                    continue
            val = ot.value_now()
            if val is not None:
                prof = (credit - val) / credit
                # the table has only (entry, stop, target).  entry = the credit
                # taken, stop = the live mark (cost to close), target = the open
                # P&L in rupees - the dashboard renders these three as
                # Entry / Mark / Open P&L for strategies that publish a mark.
                store.set_position(instrument, "options-hold", "SELL", lots,
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
                clear_state(state)
                _say("[HOLD] done. journal: " + str(store.summary()))
                return "expired"
        except Exception as e:
            _say("[HOLD] error: " + type(e).__name__ + " " + str(e)[:120])
        sleep(poll)


def main() -> None:
    """CLI: one index, one process.  options_supervisor.py is the scheduled one."""
    index = str(arg("--index", "NIFTY")).upper()
    run_session(
        index,
        lots=int(arg("--lots", LOTS)),
        short_off=int(arg("--short-off", 3)),
        wing=int(arg("--wing", 3)),
        state=arg("--state", os.path.join(REPO, "options_hold_%s.json" % index)),
        instrument=arg("--instrument", "%s 0DTE" % index),
        poll=int(arg("--poll", POLL)),
        enter_at=arg("--enter-at", None),
        enter_now="--enter" in sys.argv)


if __name__ == "__main__":
    main()
