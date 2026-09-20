"""Futures entry-path correctness in the live runner.

Three defects this locks down:
  * acting on a bar that has not closed yet (Dhan stamps bars at their start)
  * opening a position even when the broker rejected the order
  * booking the journal at the signal price rather than the actual fill
"""
import types
from datetime import datetime, timedelta

from parallax.apps.worker.live_runner import IST, LiveRunner
from parallax.contracts import Bar, OrderAck, OrderStatus, Side


def _runner(dry_run=True, **over):
    r = object.__new__(LiveRunner)
    r.bar_seconds = 300
    r.max_bar_age = 400
    r.paper_slippage = 0.5
    r.max_entry_drift = 0.002
    r.lots_futures = 3
    r._broker = lambda: types.SimpleNamespace(dry_run=dry_run)
    for k, v in over.items():
        setattr(r, k, v)
    return r


def _bar(ts):
    return Bar(ts=ts, open=100.0, high=101.0, low=99.0, close=100.5, volume=0.0)


# ---- completed-bar gate ---------------------------------------------------

def test_bar_still_forming_is_rejected():
    r = _runner()
    # stamped 2 minutes ago: a 5-minute bar is still building
    assert r._fresh(_bar(datetime.now(IST) - timedelta(seconds=120))) is False


def test_just_closed_bar_is_accepted():
    r = _runner()
    assert r._fresh(_bar(datetime.now(IST) - timedelta(seconds=305))) is True


def test_stale_bar_is_rejected():
    r = _runner()
    assert r._fresh(_bar(datetime.now(IST) - timedelta(seconds=900))) is False


def test_completion_window_is_contiguous():
    r = _runner()
    ages = [r._fresh(_bar(datetime.now(IST) - timedelta(seconds=s)))
            for s in range(0, 900, 15)]
    # accepted ages must form one block: closed, then too old - never accepted
    # again after being rejected
    first = ages.index(True)
    last = len(ages) - 1 - ages[::-1].index(True)
    assert all(ages[first:last + 1])


# ---- fill price -----------------------------------------------------------

def test_rejected_order_has_no_fill():
    r = _runner()
    ack = OrderAck("", "i", OrderStatus.REJECTED, message="margin")
    assert r._fill_price(ack, _bar(datetime.now(IST)), Side.BUY) is None


def test_live_fill_uses_the_broker_price():
    r = _runner(dry_run=False)
    ack = OrderAck("o", "i", OrderStatus.FILLED, filled_qty=195, avg_price=24012.35)
    assert r._fill_price(ack, _bar(datetime.now(IST)), Side.BUY) == 24012.35


def test_paper_fill_crosses_the_spread():
    r = _runner(dry_run=True)
    dark = types.SimpleNamespace(dry_run=True)
    r._broker = lambda: dark
    bar = _bar(datetime.now(IST))
    ack = OrderAck("DRY", "i", OrderStatus.NEW, message="dry_run")
    assert r._fill_price(ack, bar, Side.BUY) == bar.close + 0.5
    assert r._fill_price(ack, bar, Side.SELL) == bar.close - 0.5


def test_fill_is_never_the_signal_price():
    """The whole point: the booked entry must be a price the market had."""
    r = _runner()
    bar = _bar(datetime.now(IST))
    ack = OrderAck("DRY", "i", OrderStatus.NEW, message="dry_run")
    fill = r._fill_price(ack, bar, Side.BUY)
    assert fill != bar.close + 100     # a stale OTE level 100 pts away
    assert abs(fill - bar.close) <= 1.0
