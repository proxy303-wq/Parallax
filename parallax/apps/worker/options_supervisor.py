"""One supervisor process for the positional condor, in place of four services.

The schedule (parallax.config.schedule.options_plan) returns EXACTLY ONE index
per day: NIFTY on a normal Tuesday, BANKNIFTY on the last Tuesday of the month,
SENSEX on a normal Thursday, BANKEX on the last Thursday, and nothing at all on
every other day.  Four systemd units (parallax-opt-nifty, -sensex, -banknifty,
-bankex) therefore kept four processes alive 24/7 for at most one day's work,
and three of them only ever existed to race the fourth for the Dhan auth token.
On a PaaS that bills per running process this is three processes of pure waste.

This module reads the clock in IST, works out today's index itself, runs the
holder (options_hold.run_session) for it, and rolls to the next scheduled day -
in-process, so a session ending never looks like a crash to the platform, and a
platform restart lands on the same decision because the decision is a function
of the date rather than of process state.

Only one holder can run at a time, and that is enforced with a lock rather than
assumed: the schedule guarantees at most one index a day, and this guard means a
bug cannot quietly turn that into two condors on one expiry.

    python -m parallax.apps.worker.options_supervisor --lots 7
"""
from __future__ import annotations

import argparse
import os
import threading
import time
from datetime import date, datetime, time as clock, timedelta

from parallax.apps.worker.live_runner import IST
from parallax.apps.worker.options_hold import _say, run_session
from parallax.config.schedule import options_plan

#: Look again from here on a scheduled day - before the 09:15 open, so the
#: holder is waiting for its window rather than racing it.
SESSION_OPEN = clock(9, 0)
#: A holder may wait for its entry window only until the session close.  Past
#: that the day is over and the supervisor re-plans for the next scheduled day
#: instead of parking the process in a wait loop with nothing left to wait for.
SESSION_CLOSE = clock(15, 30)
#: A holder that fails is worth retrying - the entry window runs to 09:50, so a
#: transient Dhan failure at 09:20 is still recoverable - but a persistent fault
#: must not spin, so the day is written off after this many attempts.
MAX_RETRIES = 2
RETRY_AFTER = 300.0
#: Outcomes where another attempt inside the same session could still work.
RETRYABLE = ("error", "entry-failed")
#: A scheduled expiry day is never more than a week away; this bounds the search.
_WEEK = 8


def index_for(day: date, plan_fn=options_plan) -> str | None:
    """The index the scheduler trades on this date, or None when options idle."""
    plan = plan_fn(day)
    return plan[0] if plan else None


def next_session_date(day: date, plan_fn=options_plan) -> date | None:
    """The first date AFTER the given day on which an option expiry is traded."""
    d = day + timedelta(days=1)
    for _ in range(_WEEK):
        if plan_fn(d):
            return d
        d += timedelta(days=1)
    return None


def _ist_now() -> datetime:
    """Now, in IST.  Never a naive local read: the box may well be on UTC."""
    return datetime.now(IST)


class OptionsSupervisor:
    """Runs the holder for exactly one index at a time, for the process lifetime."""

    def __init__(self, lots: int = 7, short_off: int = 3, wing: int = 3,
                 state_dir: str = ".", poll: int = 30, enter_at: str = "09:20",
                 runner=None, plan_fn=options_plan, now_fn=None,
                 sleep_fn=time.sleep, retries: int = MAX_RETRIES,
                 retry_after: float = RETRY_AFTER):
        self.lots = lots
        self.short_off = short_off
        self.wing = wing
        self.state_dir = state_dir
        self.poll = max(1, int(poll))
        self.enter_at = enter_at
        self.retries = retries
        self.retry_after = retry_after
        self._runner = runner or run_session
        self._plan = plan_fn
        self._now = now_fn or _ist_now
        self._sleep = sleep_fn
        self._lock = threading.Lock()     # one holder at a time, enforced
        self._active: str | None = None
        self._done_on: date | None = None
        self._failures = 0
        self._failures_on: date | None = None

    # ---- planning ---------------------------------------------------------
    def state_path(self, index: str) -> str:
        """The holder's own per-index state file, under --state-dir."""
        return os.path.join(self.state_dir, "options_hold_%s.json" % index)

    def index_for(self, day: date) -> str | None:
        return index_for(day, self._plan)

    def next_wakeup(self, now: datetime) -> datetime:
        """09:00 IST on the next date the scheduler trades options."""
        nxt = next_session_date(now.astimezone(IST).date(), self._plan)
        if nxt is None:                  # unreachable: the schedule repeats weekly
            return now + timedelta(seconds=self.poll)
        return datetime.combine(nxt, SESSION_OPEN, tzinfo=IST)

    def _sleep_until(self, target: datetime) -> None:
        """Sleep in poll-sized slices rather than one long block.

        A wait to the next scheduled day is days long and it has to survive the
        machine suspending, a clock correction and the IST date rolling over.
        Re-reading the clock after every slice is what makes waking on the right
        day true instead of assumed; the slices also keep this off a busy loop.
        """
        while True:
            remaining = (target - self._now()).total_seconds()
            if remaining <= 0:
                return
            self._sleep(min(remaining, float(self.poll)))

    # ---- one session ------------------------------------------------------
    def _run_index(self, index: str, day: date) -> str:
        """Run the holder for this index, holding the single-holder lock."""
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("a holder is already running (%s) - refusing to "
                               "start %s" % (self._active, index))
        try:
            assert self._active is None, self._active
            self._active = index
            _say("[SUP] %s session for %s" % (index, day.strftime("%a %d %b")))
            return self._runner(
                index, lots=self.lots, short_off=self.short_off, wing=self.wing,
                state=self.state_path(index),
                instrument="%s 0DTE" % index, poll=self.poll,
                enter_at=self.enter_at, enter_now=True,
                deadline=datetime.combine(day, SESSION_CLOSE, tzinfo=IST)) or "done"
        finally:
            self._active = None
            self._lock.release()

    def run_once(self, now: datetime | None = None) -> str | None:
        """One planning pass: run today's holder if the schedule has one.

        Returns the holder's outcome, or None when nothing is due (or today has
        already been dealt with).
        """
        now = now or self._now()
        day = now.astimezone(IST).date()
        index = self.index_for(day)
        if index is None:
            _say("[SUP] %s - not an options expiry day" % day.strftime("%a %d %b"))
            return None
        if self._failures_on != day:
            self._failures_on = day
            self._failures = 0
        if self._done_on == day:
            return None
        try:
            outcome = self._run_index(index, day)
        except Exception as e:
            # the platform would restart a process that dies, and a restart is
            # not the recovery here - the next attempt is
            outcome = "error"
            _say("[SUP] %s holder raised: %s %s"
                 % (index, type(e).__name__, str(e)[:140]))
        if outcome in RETRYABLE and self._failures < self.retries:
            self._failures += 1
            _say("[SUP] %s %s - attempt %d/%d, retrying in %ds"
                 % (index, outcome, self._failures, self.retries, self.retry_after))
            self._sleep(self.retry_after)
            return outcome
        self._done_on = day
        _say("[SUP] %s %s for %s - next session %s"
             % (index, outcome, day.strftime("%a %d %b"),
                (next_session_date(day, self._plan) or day).strftime("%a %d %b")))
        return outcome

    def run(self, max_seconds: float | None = None) -> None:
        """Plan, run the due index, roll to the next scheduled day - forever."""
        t0 = time.time()
        _say("[SUP] options supervisor online (lots %d, %d-%d, poll %ds, "
             "entry %s IST, state dir %s)"
             % (self.lots, self.short_off, self.wing, self.poll, self.enter_at,
                self.state_dir))
        while True:
            try:
                now = self._now()
                if self.run_once(now) is None:
                    target = self.next_wakeup(now)
                    _say("[SUP] no session on %s - sleeping until %s IST"
                         % (now.astimezone(IST).strftime("%a %d %b"),
                            target.strftime("%a %d %b %H:%M")))
                    self._sleep_until(target)
            except Exception as e:
                # survive in-process: a crash here would restart-loop the whole
                # supervisor and lose the plan for the day it was in
                _say("[SUP] error: %s %s" % (type(e).__name__, str(e)[:140]))
                self._sleep(self.poll)
            if max_seconds and time.time() - t0 > max_seconds:
                return


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="One supervisor for the positional condor schedule")
    p.add_argument("--lots", type=int, default=7)
    p.add_argument("--short-off", dest="short_off", type=int, default=3,
                   help="strikes out for the sold legs")
    p.add_argument("--wing", type=int, default=3,
                   help="strikes beyond the shorts for the bought legs")
    p.add_argument("--state-dir", dest="state_dir", default=".",
                   help="where options_hold_<INDEX>.json lives")
    p.add_argument("--poll", type=int, default=30)
    p.add_argument("--enter-at", dest="enter_at", default="09:20",
                   help="entry window start, IST, HH:MM")
    return p


def main() -> None:
    a = _parser().parse_args()
    OptionsSupervisor(lots=a.lots, short_off=a.short_off, wing=a.wing,
                      state_dir=a.state_dir, poll=a.poll,
                      enter_at=a.enter_at).run()


if __name__ == "__main__":
    main()
