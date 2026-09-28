"""The two one-off flags for a deliberate two-index session.

--force-entry runs an index the schedule did not pick, and --allow-stack exempts
the run from the guard that stops two options positions being open at once.
Both exist because on a last Tuesday the plan returns BANKNIFTY alone, so a
second index needs the schedule overridden AND the stacking guard lifted.

These are risk controls being switched off on purpose, so they get tests.
"""
import datetime

import pytest

from parallax.apps.worker import options_hold as oh

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
DAY = datetime.date(2026, 9, 29)          # last Tuesday: plan is BANKNIFTY only
ENTRY = datetime.datetime(2026, 9, 29, 9, 30, tzinfo=IST)
SETTLE = datetime.datetime(2026, 9, 29, 15, 30, tzinfo=IST)
PLAN = {"legs": {"a": 1}, "credit": 100.0, "atm": 100.0, "iv": 0.1,
        "realized": 0.1, "expiry": DAY.isoformat()}


class Store:
    """Every method run_session touches.  A missing one is not a clean failure:
    it throws inside the worker's while True, gets swallowed, and spins."""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.mode_value = "paper"

    def mode(self):
        return self.mode_value

    def positions(self):
        return self.rows

    def summary(self):
        return {}

    def set_position(self, *a, **k):
        pass

    def record_trade(self, *a, **k):
        pass

    def clear_position(self, *a, **k):
        pass


class FakeCondor:
    entered = False

    def __init__(self, broker=None, lots=7, **kw):
        self.broker = broker
        self.lot, self.step, self.lots = 65, 50.0, lots
        self.active = None

    def select(self, **kw):
        return dict(PLAN)

    def enter(self, plan):
        type(self).entered = True
        self.active = {"plan": plan}

    def value_now(self):
        return None

    def expiry_reached(self, now):
        return True

    def at_settlement(self, now):
        return True

    def close(self, *a, **k):
        self.active = None


class Rig:
    def __init__(self, store, condor, tmp_path):
        self.store = store
        self.condor = condor
        self.tmp = tmp_path

    def run(self, **kw):
        FakeCondor.entered = False
        calls = {"n": 0}

        def now_fn():
            calls["n"] += 1
            return ENTRY if calls["n"] <= 1 else SETTLE

        return oh.run_session(
            index=kw.pop("index", "NIFTY"), lots=8, short_off=5, wing=3,
            state=str(self.tmp / "h.json"),
            instrument=kw.pop("instrument", "NIFTY 0DTE"),
            poll=0, enter_at="09:30", enter_now=True,
            plan_fn=kw.pop("plan_fn", lambda now: ("BANKNIFTY",)),
            now_fn=now_fn, sleep=lambda s: None, **kw)


@pytest.fixture
def rig(monkeypatch, tmp_path):
    from parallax.apps.worker import dhan_options_live as live
    store = Store()
    monkeypatch.setattr(live, "ZeroDteCondor", FakeCondor)
    monkeypatch.setattr(oh, "DhanBroker", lambda **k: type(
        "B", (), {"connect": lambda s: True, "_auth_error": None})())
    monkeypatch.setattr(oh, "funds_report", lambda *a, **k: None)
    monkeypatch.setattr(oh, "_say", lambda m: None)
    monkeypatch.setattr("parallax.web.store.JournalStore", lambda *a, **k: store)
    return Rig(store, FakeCondor, tmp_path)


def test_nifty_waits_when_the_plan_says_banknifty(rig):
    """The default, and the reason NIFTY would otherwise not trade at all."""
    assert rig.run(deadline=SETTLE) == "deadline"
    assert rig.condor.entered is False


def test_force_entry_runs_an_index_off_the_plan(rig):
    assert rig.run(force_entry=True) == "expired"
    assert rig.condor.entered is True


def test_a_foreign_position_blocks_entry(rig):
    rig.store.rows = [{"instrument": "BANKNIFTY 0DTE", "strategy": "options-hold"}]
    assert rig.run(force_entry=True) == "position-held"
    assert rig.condor.entered is False


def test_allow_stack_lets_the_second_index_in(rig):
    rig.store.rows = [{"instrument": "BANKNIFTY 0DTE", "strategy": "options-hold"}]
    assert rig.run(force_entry=True, allow_stack=True) == "expired"
    assert rig.condor.entered is True


def test_our_own_row_never_blocks_us(rig):
    rig.store.rows = [{"instrument": "NIFTY 0DTE", "strategy": "options-hold"}]
    assert rig.run(force_entry=True) == "expired"
