"""The PAPER/LIVE switch.

This is the only control on the dashboard that can put real money at risk, so
these test what it DOES rather than what it says.

Two bugs are pinned here:

  * The button used to offer "Switch to DEMO".  MODES was a three-way cycle
    (paper -> demo -> live), and "demo" was the Delta crypto testnet - a venue
    that is stopped and has nothing to do with the options book, where demo and
    paper were the same thing anyway.  So the one button the operator wanted
    landed them on a state that changed nothing and hid LIVE an extra click away.

  * Flipping it did not work.  options_hold captured the mode into the broker at
    process start, and those workers run for weeks under Restart=always, so the
    switch was inert until someone restarted four services by hand.
"""
from __future__ import annotations

import datetime
import importlib
import types

import pytest
from fastapi.testclient import TestClient

# NOT "import parallax.web.app as web": parallax/web/__init__.py does
# "from .app import app", which rebinds the package attribute to the FastAPI
# instance and shadows the module, so the name would come back as the app.
web = importlib.import_module("parallax.web.app")
from parallax.web.store import JournalStore

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
DAY = datetime.date(2026, 9, 29)                 # a Tuesday: NIFTY's expiry
ENTRY = datetime.datetime(2026, 9, 29, 9, 30, tzinfo=IST)
SETTLE = datetime.datetime(2026, 9, 29, 15, 30, tzinfo=IST)

PLAN = {"legs": {"put_short": {"strike": 23000}, "call_short": {"strike": 23200},
                 "put_hedge": {"strike": 22800}, "call_hedge": {"strike": 23400}},
        "credit": 100.0, "atm": 23100.0, "iv": 0.12, "realized": 0.10,
        "expiry": DAY.isoformat()}


@pytest.fixture
def st(tmp_path, monkeypatch):
    s = JournalStore(path=str(tmp_path / "journal.sqlite"))
    monkeypatch.setattr(web, "store", s)
    return s


@pytest.fixture
def client(st):
    return TestClient(web.app)


# ---- the store: two modes, and the safe default ---------------------------

def test_it_starts_in_paper(st):
    assert st.mode() == "paper"


def test_the_button_offers_live_not_demo(st):
    assert st.next_mode() == "live"


def test_it_toggles_straight_back(st):
    st.set_mode("live")
    assert st.mode() == "live"
    assert st.next_mode() == "paper"


def test_a_legacy_demo_row_reads_as_paper(st):
    """A row written before the third mode was dropped must not arm orders."""
    st.set_setting("mode", "demo")
    assert st.mode() == "paper"
    assert st.next_mode() == "live"


def test_junk_lands_on_paper(st):
    """Unknown means 'simulate', never 'trade'."""
    for bad in ("", "   ", "LIVEX", "null", "TRUE", "demo"):
        st.set_setting("mode", bad)
        assert st.mode() == "paper", bad


def test_live_is_recognised_whatever_the_case(st):
    for good in ("live", "LIVE", "Live", " live "):
        st.set_setting("mode", good)
        assert st.mode() == "live", good


def test_set_mode_never_writes_demo(st):
    assert st.set_mode("demo") == "paper"
    assert st.get_setting("mode") == "paper"


# ---- the dashboard --------------------------------------------------------

def test_paper_shows_a_switch_to_live(client):
    html = client.get("/").text
    assert "Switch to LIVE" in html
    assert "Switch to PAPER" not in html


def test_the_old_demo_button_is_gone(client):
    html = client.get("/").text
    assert "DEMO" not in html
    assert "demo" not in html.lower()


def test_live_shows_a_switch_back_to_paper(client, st):
    st.set_mode("live")
    html = client.get("/").text
    assert "Switch to PAPER" in html
    assert "Switch to LIVE" not in html


def test_going_live_asks_first(client):
    """The page has no login, so the one irreversible click confirms."""
    html = client.get("/").text
    assert "confirm(" in html


def test_one_click_reaches_live(client, st):
    r = client.post("/mode", data={"mode": "live"}, follow_redirects=False)
    assert r.status_code == 303
    assert st.mode() == "live"


def test_an_unknown_posted_mode_lands_on_paper(client, st):
    st.set_mode("live")
    client.post("/mode", data={"mode": "../../etc/passwd"}, follow_redirects=False)
    assert st.mode() == "paper"


def test_the_api_reports_the_normalised_mode(client, st):
    client.post("/mode", data={"mode": "live"}, follow_redirects=False)
    assert client.get("/api/state").json()["mode"] == "live"


# ---- the workers must act on it -------------------------------------------

class FakeStore:
    """Mode that can change while the worker is running."""

    def __init__(self, mode="paper"):
        self._mode = mode
        self.summary_calls = 0

    def mode(self):
        return self._mode

    def positions(self):
        return []

    def set_position(self, *a, **k):
        pass

    def summary(self):
        self.summary_calls += 1
        return {}


@pytest.fixture
def worker(monkeypatch, tmp_path):
    from parallax.apps.worker import dhan_options_live as live
    from parallax.apps.worker import options_hold as oh

    store = FakeStore("paper")
    monkeypatch.setattr("parallax.web.store.JournalStore", lambda *a, **k: store)
    monkeypatch.setattr(oh, "DhanBroker", lambda **k: types.SimpleNamespace(
        connect=lambda: True, _auth_error=None, **k))
    monkeypatch.setattr(oh, "funds_report", lambda *a, **k: None)
    monkeypatch.setattr(oh, "_say", lambda m: None)

    class FakeCondor:
        entry_dry = "never entered"

        def __init__(self, broker=None, lots=7, **kw):
            self.broker = broker
            self.lot, self.step, self.lots = 65, 50.0, lots
            self.active = None
            # The operator clicks LIVE in the seconds after the worker boots -
            # i.e. after the broker was already built from the boot-time mode.
            store._mode = "live"

        def select(self, **kw):
            return dict(PLAN)

        def enter(self, plan):
            type(self).entry_dry = self.broker.dry_run
            self.active = {"plan": plan}

        def value_now(self):
            return None

        def expiry_reached(self, now):
            return True

        def at_settlement(self, now):
            return True

        def close(self, *a, **k):
            self.active = None

        def _start_feed(self, plan):
            pass

    monkeypatch.setattr(live, "ZeroDteCondor", FakeCondor)

    calls = {"n": 0}

    def now_fn():
        calls["n"] += 1
        return ENTRY if calls["n"] <= 1 else SETTLE

    def run():
        return oh.run_session(
            index="NIFTY", lots=7, short_off=4, wing=3,
            state=str(tmp_path / "hold.json"), instrument="NIFTY 0DTE",
            poll=0, enter_at="09:30", enter_now=True,
            plan_fn=lambda now: ("NIFTY",), now_fn=now_fn, sleep=lambda s: None)

    return types.SimpleNamespace(run=run, condor=FakeCondor, store=store)


def test_the_mode_is_read_when_the_order_goes_out(worker):
    """The regression: a mode cached at boot made the switch do nothing."""
    assert worker.run() == "expired"
    assert worker.condor.entry_dry is False, (
        "the broker was still in dry_run at entry - the LIVE switch did nothing")


def test_a_worker_that_stays_in_paper_still_simulates(worker, monkeypatch):
    """The converse: no switch, no orders.  Without this the test above would
    pass just as well if dry_run had been hardcoded to False."""
    from parallax.apps.worker import dhan_options_live as live

    class StillPaper(worker.condor):
        def __init__(self, broker=None, lots=7, **kw):
            super().__init__(broker=broker, lots=lots, **kw)
            worker.store._mode = "paper"          # the switch never happens

    monkeypatch.setattr(live, "ZeroDteCondor", StillPaper)
    assert worker.run() == "expired"
    assert StillPaper.entry_dry is True
