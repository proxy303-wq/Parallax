"""When the expiry exit fires, and whether it trades into a closed market.

The exit used to be 15:15.  These are cash-settled European index options, so
at 15:30 they settle by themselves and there is nothing to send.  Carrying the
last 15 minutes is worth real money -- at 15:15 the legs still hold 15 minutes
of time value, and closing there buys it back.  Measured over 71 NIFTY and 57
SENSEX expiries: +Rs 24,911 and +Rs 32,872 versus settling.
"""
import datetime

import pytest

from parallax.apps.worker import dhan_options_live as live

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
EXPIRY = datetime.date(2026, 10, 1)


class FakeBroker:
    def __init__(self):
        self.orders = []

    def place_option_order(self, contract, side, lots, order_type):
        self.orders.append((side, contract.strike))
        return None


class FakeJournal:
    def __init__(self):
        self.trades = []
        self.cleared = []

    def record_trade(self, *a, **k):
        self.trades.append((a, k))

    def clear_position(self, name):
        self.cleared.append(name)


def at(hh, mm, day=EXPIRY):
    return datetime.datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


@pytest.fixture()
def condor(monkeypatch):
    import parallax.web.store as store
    monkeypatch.setattr(store, "JournalStore", lambda *a, **k: FakeJournal())
    monkeypatch.setattr(live, "TelegramBot", lambda *a, **k: type(
        "T", (), {"configured": False, "send": lambda s, m: None})())
    monkeypatch.setattr(live.ZeroDteCondor, "_say", lambda self, m: None)
    monkeypatch.setattr(live.ZeroDteCondor, "_stop_feed", lambda self: None)
    c = live.ZeroDteCondor(broker=FakeBroker(), lots=7, index="SENSEX")
    c.active = {"plan": {
        "credit": 100.50,
        "expiry": EXPIRY.isoformat(),
        "legs": {
            "put_short": {"strike": 55100, "type": "PE", "security_id": "1"},
            "call_short": {"strike": 56100, "type": "CE", "security_id": "2"},
            "put_hedge": {"strike": 54800, "type": "PE", "security_id": "3"},
            "call_hedge": {"strike": 56400, "type": "CE", "security_id": "4"},
        }}}
    c.last_value = 60.0
    c.last_pnl = (100.50 - 60.0) * c.lot * c.lots
    return c


def test_it_no_longer_exits_at_1515(condor):
    """The old rule threw away the last 15 minutes of theta."""
    assert condor.expiry_reached(at(15, 15)) is False
    assert condor.at_settlement(at(15, 15)) is False


def test_it_holds_until_the_1530_close(condor):
    assert condor.expiry_reached(at(15, 29)) is False
    assert condor.at_settlement(at(15, 30)) is True
    assert condor.expiry_reached(at(15, 30)) is True


def test_settling_sends_no_orders(condor):
    """At 15:30 the contracts have settled; sending orders would deal into the
    NEXT expiry.  The position closes on the exchange's settlement instead."""
    condor.close("expiry", settle=True)
    assert condor.broker.orders == []
    assert condor.journal.cleared == ["SENSEX 0DTE"]
    assert condor.active is None


def test_an_overdue_position_is_still_flattened_with_orders(condor):
    """A contract that already settled cannot be closed the next morning, but a
    worker that missed its own expiry day must not sit on the position."""
    later = EXPIRY + datetime.timedelta(days=1)
    now = at(10, 0, later)
    assert condor.expiry_reached(now) is True
    assert condor.at_settlement(now) is False       # not the settle moment
    condor.close("expiry", settle=condor.at_settlement(now))
    assert len(condor.broker.orders) == 4


def test_a_normal_close_still_trades_all_four_legs(condor):
    condor.close("tp")
    assert len(condor.broker.orders) == 4
    assert condor.broker.orders.count(("BUY", 55100)) == 1
    assert condor.broker.orders.count(("SELL", 54800)) == 1
