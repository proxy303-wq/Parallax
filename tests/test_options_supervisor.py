"""Schedule-driven routing for the single options supervisor.

Four per-index services (parallax-opt-nifty, -sensex, -banknifty, -bankex) used
to run 24/7 for at most one day's work between them, and three of the four only
ever raced the others for the Dhan token.  One process now works out which index
is due from the session schedule itself, so what these tests pin down is that
decision - the part that used to live in systemd unit names.

    normal Tuesday   NIFTY        last Tuesday   BANKNIFTY
    normal Thursday  SENSEX       last Thursday  BANKEX
    anything else    nothing      (the futures engine's days)
"""
import datetime
import os

import pytest

from parallax.apps.worker import options_hold
from parallax.apps.worker.options_supervisor import (
    IST, OptionsSupervisor, _parser, index_for, next_session_date,
)
from parallax.config.schedule import options_plan

D = datetime.date
UTC = datetime.timezone.utc
DAY = datetime.timedelta(days=1)

#: The four shapes the schedule distinguishes, plus the two kinds of idle day.
NORMAL_TUE = D(2026, 9, 1)
LAST_TUE = D(2026, 9, 29)
NORMAL_THU = D(2026, 9, 3)
LAST_THU = D(2026, 9, 24)
WEDNESDAY = D(2026, 9, 2)          # futures day
FRIDAY = D(2026, 9, 4)             # futures day
SATURDAY = D(2026, 9, 5)
SUNDAY = D(2026, 9, 6)


def at(day: D, hh: int = 9, mm: int = 25) -> datetime.datetime:
    """An IST timestamp on that date - the only clock the supervisor may read."""
    return datetime.datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


class Recorder:
    """Stands in for options_hold.run_session and remembers how it was called."""

    def __init__(self, outcome: str = "expired", error: Exception | None = None):
        self.calls: list = []
        self.outcome = outcome
        self.error = error

    def __call__(self, index: str, **kwargs):
        self.calls.append((index, kwargs))
        if self.error is not None:
            raise self.error
        return self.outcome

    def indices(self) -> list:
        return [c[0] for c in self.calls]


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """The supervisor reports to Telegram in production; not from a test run."""
    monkeypatch.setattr("parallax.apps.worker.options_supervisor._say",
                        lambda msg: None)


def test_ist_is_not_local_time():
    """The box may be on UTC; market hours are IST and nothing else."""
    assert IST.utcoffset(None) == datetime.timedelta(hours=5, minutes=30)


# ---- the schedule decision ------------------------------------------------
def test_the_four_expiry_shapes_pick_the_four_indices():
    assert index_for(NORMAL_TUE) == "NIFTY"
    assert index_for(LAST_TUE) == "BANKNIFTY"
    assert index_for(NORMAL_THU) == "SENSEX"
    assert index_for(LAST_THU) == "BANKEX"


def test_weekend_and_futures_days_have_no_session():
    for day in (WEDNESDAY, FRIDAY, SATURDAY, SUNDAY):
        assert options_plan(day) == []
        assert index_for(day) is None


def test_the_supervisor_picks_exactly_what_options_plan_returns():
    """Whatever the schedule says - for every day of seven months - is what runs."""
    sup = OptionsSupervisor()
    day = D(2026, 9, 1)
    while day <= D(2027, 3, 31):
        expected = options_plan(day)
        assert sup.index_for(day) == (expected[0] if expected else None), day
        day += DAY


def test_the_supervisor_asks_the_schedule_about_the_ist_date():
    seen: list = []

    def plan(day):
        seen.append(day)
        return options_plan(day)

    sup = OptionsSupervisor(plan_fn=plan)
    assert sup.index_for(NORMAL_TUE) == "NIFTY"
    assert seen == [NORMAL_TUE]


def test_the_next_session_skips_weekends_and_futures_days():
    assert next_session_date(WEDNESDAY) == NORMAL_THU     # Wed -> Thu, not Fri
    assert next_session_date(NORMAL_TUE) == NORMAL_THU    # Tue -> Thu: Wed is idle
    assert next_session_date(FRIDAY) == D(2026, 9, 8)     # Fri -> next Tuesday
    assert next_session_date(SATURDAY) == D(2026, 9, 8)   # Sun and Mon are idle
    assert next_session_date(SUNDAY) == D(2026, 9, 8)


def test_the_wakeup_is_0900_ist_on_the_next_scheduled_day():
    sup = OptionsSupervisor()
    target = sup.next_wakeup(at(SATURDAY, 11, 0))
    assert target == datetime.datetime(2026, 9, 8, 9, 0, tzinfo=IST)
    assert target.utcoffset() == datetime.timedelta(hours=5, minutes=30)


# ---- what the supervisor does with that decision --------------------------
def test_a_non_expiry_day_starts_no_holder():
    r = Recorder()
    sup = OptionsSupervisor(runner=r)
    assert sup.run_once(at(WEDNESDAY)) is None
    assert sup.run_once(at(SATURDAY)) is None
    assert r.calls == []


def test_the_holder_runs_for_todays_index_with_the_configured_shape():
    r = Recorder()
    sup = OptionsSupervisor(lots=4, short_off=3, wing=3, state_dir="state",
                            poll=30, enter_at="09:20", runner=r,
                            sleep_fn=lambda s: None)
    assert sup.run_once(at(NORMAL_TUE)) == "expired"
    assert r.indices() == ["NIFTY"]
    _, kw = r.calls[0]
    assert kw["lots"] == 4 and kw["short_off"] == 3 and kw["wing"] == 3
    assert kw["poll"] == 30 and kw["enter_at"] == "09:20"
    assert kw["enter_now"] is True
    assert kw["state"] == os.path.join("state", "options_hold_NIFTY.json")
    assert kw["instrument"] == "NIFTY 0DTE"
    # the holder may only wait for its entry window until today's close, else a
    # process started after the window would park itself into tomorrow's index
    assert kw["deadline"] == datetime.datetime(2026, 9, 1, 15, 30, tzinfo=IST)


def test_the_monthly_indices_take_their_own_day():
    r = Recorder()
    sup = OptionsSupervisor(runner=r)
    sup.run_once(at(LAST_TUE))
    sup.run_once(at(LAST_THU))
    assert r.indices() == ["BANKNIFTY", "BANKEX"]


def test_a_finished_session_is_not_restarted_the_same_day():
    r = Recorder()
    sup = OptionsSupervisor(runner=r)
    assert sup.run_once(at(NORMAL_TUE)) == "expired"
    assert sup.run_once(at(NORMAL_TUE, 10, 0)) is None
    assert r.indices() == ["NIFTY"]


def test_the_supervisor_plans_on_the_ist_date_not_the_utc_one():
    """20:00 UTC on Tuesday is 01:30 IST on Wednesday - not an options day."""
    r = Recorder()
    sup = OptionsSupervisor(runner=r)
    assert sup.run_once(datetime.datetime(2026, 9, 1, 20, 0, tzinfo=UTC)) is None
    assert r.calls == []
    # 04:00 UTC is 09:30 IST on the Tuesday itself - the NIFTY session
    assert sup.run_once(datetime.datetime(2026, 9, 1, 4, 0, tzinfo=UTC)) == "expired"
    assert r.indices() == ["NIFTY"]


def test_only_one_holder_can_run_at_a_time():
    started: list = []
    sup = None

    def runner(index, **kwargs):
        started.append(index)
        with pytest.raises(RuntimeError):        # the lock, not a convention
            sup._run_index("SENSEX", NORMAL_THU)
        return "expired"

    sup = OptionsSupervisor(runner=runner)
    assert sup.run_once(at(NORMAL_TUE)) == "expired"
    assert started == ["NIFTY"]


# ---- resilience -----------------------------------------------------------
def test_a_raising_holder_is_retried_then_the_day_is_written_off():
    r = Recorder(error=RuntimeError("dhan 500"))
    slept: list = []
    sup = OptionsSupervisor(runner=r, sleep_fn=slept.append, retries=1)
    assert sup.run_once(at(NORMAL_TUE)) == "error"
    assert sup.run_once(at(NORMAL_TUE)) == "error"          # the one retry
    assert sup.run_once(at(NORMAL_TUE)) is None             # written off, no spin
    assert len(r.calls) == 2
    assert slept == [300.0]


def test_the_run_loop_survives_a_holder_that_keeps_failing():
    r = Recorder(error=RuntimeError("boom"))
    clockbox = {"t": at(NORMAL_TUE)}
    sup = OptionsSupervisor(
        runner=r, retries=0, now_fn=lambda: clockbox["t"],
        sleep_fn=lambda s: clockbox.__setitem__(
            "t", clockbox["t"] + datetime.timedelta(seconds=s)))
    sup.run(max_seconds=0.2)          # returns instead of dying, and rolls on
    assert len(r.calls) > 1
    assert not sup._lock.locked()     # no holder left behind


def test_the_wait_to_the_next_session_is_sliced_and_rereads_the_clock():
    clockbox = {"t": at(SATURDAY, 10, 0)}
    slept: list = []

    def sleep(seconds):
        slept.append(seconds)
        clockbox["t"] += datetime.timedelta(seconds=seconds)

    sup = OptionsSupervisor(now_fn=lambda: clockbox["t"], sleep_fn=sleep, poll=30)
    target = sup.next_wakeup(clockbox["t"])
    sup._sleep_until(target)
    assert clockbox["t"] >= target                  # woke on the right day
    assert max(slept) <= 30                         # never one long blind block
    assert sum(slept) > 2 * 86400                   # ...and not a 60s hot loop


# ---- the holder's own contract, as the supervisor calls it ---------------
class _FakeCondor:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.active = None
        self.lot = 65
        self.step = 50.0

    def select(self, force=False, short_off=None, wing=None) -> dict:
        return {}


@pytest.fixture()
def offline_holder(monkeypatch, tmp_path):
    """run_session with the broker, the condor and the journal faked out."""
    import parallax.apps.worker.dhan_options_live as live
    import parallax.web.store as store

    monkeypatch.setattr(live, "ZeroDteCondor", _FakeCondor)
    monkeypatch.setattr(store, "JournalStore",
                        lambda *a, **k: type("S", (), {"mode": lambda s: "paper"})())
    monkeypatch.setattr(options_hold, "DhanBroker", lambda *a, **k: None)
    monkeypatch.setattr(options_hold, "_say", lambda msg: None)
    return str(tmp_path / "options_hold_NIFTY.json")


def test_the_holder_stands_down_when_its_entry_window_has_gone(offline_holder):
    """A supervisor restarted at 16:00 must not sit waiting for tomorrow."""
    out = options_hold.run_session(
        "NIFTY", state=offline_holder, enter_now=True, enter_at="09:20",
        deadline=at(NORMAL_TUE, 15, 30), now_fn=lambda: at(NORMAL_TUE, 16, 0),
        sleep=lambda s: None)
    assert out == "deadline"


def test_the_holder_enters_inside_the_window_it_is_given(offline_holder):
    out = options_hold.run_session(
        "NIFTY", state=offline_holder, enter_now=True, enter_at="09:20",
        deadline=at(NORMAL_TUE, 15, 30), now_fn=lambda: at(NORMAL_TUE, 9, 25),
        sleep=lambda s: None)
    assert out == "entry-failed"            # it reached the entry, past the wait


def test_the_holder_waits_when_it_is_early(offline_holder):
    """09:00 is before the 09:20 window, so the holder waits for it - but only
    inside its own session, which is what the deadline is for."""
    st = {"t": at(NORMAL_TUE, 9, 0)}
    calls = {"n": 0}

    def sleep(seconds):
        calls["n"] += 1
        st["t"] = at(NORMAL_TUE, 16, 0)      # jumped past the window entirely

    out = options_hold.run_session(
        "NIFTY", state=offline_holder, enter_now=True, enter_at="09:20",
        deadline=at(NORMAL_TUE, 15, 30), now_fn=lambda: st["t"], sleep=sleep)
    assert calls["n"] == 1
    assert out == "deadline"


# ---- the runner/positional mutual exclusion ------------------------------
def _offline_holder_with(monkeypatch, rows):
    """run_session faked out, with `positions` reporting the given rows."""
    import parallax.apps.worker.dhan_options_live as live
    import parallax.web.store as store

    monkeypatch.setattr(live, "ZeroDteCondor", _FakeCondor)
    monkeypatch.setattr(store, "JournalStore", lambda *a, **k: type(
        "S", (), {"mode": lambda s: "paper",
                  "positions": lambda s: rows})())
    monkeypatch.setattr(options_hold, "DhanBroker", lambda *a, **k: None)
    monkeypatch.setattr(options_hold, "_say", lambda msg: None)


def test_the_holder_stands_down_when_the_runner_already_holds_a_condor(
        monkeypatch, tmp_path):
    """live_runner's intraday condor and this positional one both sell a NIFTY
    0DTE condor, and they used to share one positions row.  The runner defers
    to any open options row; this is the mirror of that check, so the two
    engines can never stack on the same market."""
    _offline_holder_with(monkeypatch, [
        {"instrument": "NIFTY 0DTE INTRADAY", "strategy": "options"}])
    out = options_hold.run_session(
        "NIFTY", state=str(tmp_path / "n.json"), enter_now=True,
        enter_at="09:20", deadline=at(NORMAL_TUE, 15, 30),
        now_fn=lambda: at(NORMAL_TUE, 9, 25), sleep=lambda s: None)
    assert out == "position-held"


def test_the_holder_is_not_blocked_by_its_own_position_row(
        monkeypatch, tmp_path):
    """Our own row is keyed on our own instrument, so it must not stop us -
    otherwise a restart mid-position would refuse to do anything."""
    _offline_holder_with(monkeypatch, [
        {"instrument": "NIFTY 0DTE", "strategy": "options-hold"}])
    out = options_hold.run_session(
        "NIFTY", state=str(tmp_path / "n.json"), enter_now=True,
        enter_at="09:20", deadline=at(NORMAL_TUE, 15, 30),
        now_fn=lambda: at(NORMAL_TUE, 9, 25), sleep=lambda s: None)
    assert out == "entry-failed"          # it reached the entry, past the guard


def test_a_non_options_position_does_not_block_the_holder(
        monkeypatch, tmp_path):
    """Futures and crypto rows are not option condors and are none of our
    business - the guard keys on the strategy, not on 'something is open'."""
    _offline_holder_with(monkeypatch, [
        {"instrument": "NIFTY", "strategy": "futures"},
        {"instrument": "BTCUSD", "strategy": "crypto"}])
    out = options_hold.run_session(
        "NIFTY", state=str(tmp_path / "n.json"), enter_now=True,
        enter_at="09:20", deadline=at(NORMAL_TUE, 15, 30),
        now_fn=lambda: at(NORMAL_TUE, 9, 25), sleep=lambda s: None)
    assert out == "entry-failed"


# ---- the CLI the PaaS starts ---------------------------------------------
def test_the_cli_defaults_are_the_production_ones():
    a = _parser().parse_args([])
    assert (a.lots, a.short_off, a.wing, a.state_dir, a.poll, a.enter_at) == (
        4, 3, 3, ".", 30, "09:20")
