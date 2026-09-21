"""Futures entry-path correctness in the live runner.

Three defects this locks down:
  * acting on a bar that has not closed yet (Dhan stamps bars at their start)
  * opening a position even when the broker rejected the order
  * booking the journal at the signal price rather than the actual fill
"""
import types
from datetime import datetime, timedelta

from parallax.apps.worker.live_runner import FUTURES_EXIT, IST, LiveRunner
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


# ---- exit policy ----------------------------------------------------------

def test_futures_exit_has_no_breakeven_lock():
    """The 0.5R lock is reached by noise on 5m NIFTY and was costing ~2.2x PF."""
    assert FUTURES_EXIT.lock_r == float("inf")
    assert FUTURES_EXIT.trail_r == float(0.0)


def test_unlocked_exit_never_moves_the_stop():
    from parallax.core.execution import ExitManager
    from parallax.contracts import Side
    em = ExitManager(Side.BUY, 24000.0, 23970.0, FUTURES_EXIT, target=24100.0)
    px = None
    for h, l in ((24020.0, 24010.0), (24090.0, 24070.0), (24050.0, 23960.0)):
        px, reason = em.update(h, l)
        if px is None:
            assert em.stop == 23970.0          # never dragged to breakeven
    assert px == 23970.0 and reason == "stop"  # a 0.9R winner given back, as
                                               # designed: stop + DOL only


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


# ---- end-of-day time stop ------------------------------------------------

def _flat_runner(entry=24000.0, side=Side.BUY, qty=3):
    r = _runner()
    r.bars = [_bar(datetime.now(IST))]
    r.instrument = "NSE:NIFTY"
    r.eod_exit_hm = (15, 15)
    r.gates = {"trades": 0}
    r.recorded = []
    r.said = []
    r.store = types.SimpleNamespace(
        record_trade=lambda *a, **k: r.recorded.append(a))
    r._say = lambda m: r.said.append(m)
    r.active = {"side": side, "entry": entry, "qty": qty,
                "em": types.SimpleNamespace(update=lambda h, l: (None, ""))}
    return r


def test_no_flatten_before_the_cutoff():
    r = _flat_runner()
    r._futures_housekeeping(datetime(2026, 8, 4, 15, 10, tzinfo=IST))
    assert r.active is not None
    assert not r.recorded


def test_flatten_at_the_cutoff():
    r = _flat_runner()
    r._futures_housekeeping(datetime(2026, 8, 4, 15, 15, tzinfo=IST))
    assert r.active is None
    assert len(r.recorded) == 1


def test_flatten_after_the_cutoff():
    r = _flat_runner()
    r._futures_housekeeping(datetime(2026, 8, 4, 15, 40, tzinfo=IST))
    assert r.active is None


def test_housekeeping_is_a_noop_when_flat():
    r = _flat_runner()
    r.active = None
    r._futures_housekeeping(datetime(2026, 8, 4, 15, 30, tzinfo=IST))
    assert not r.recorded


# ---- exit accounting ------------------------------------------------------

def test_close_futures_nets_fees_off_the_pnl():
    r = _flat_runner(entry=24000.0)
    r._close_futures(24050.0, "managed")
    # gross = 50 pts x 3 lots x 65 = 9,750 ; fees = 0.01% of (24000+24050) x 195
    gross = 50.0 * 3 * 65
    fees = (24000.0 + 24050.0) * 195 * 0.0001
    assert abs(r.recorded[0][6] - (gross - fees)) < 0.01
    assert r.recorded[0][6] < gross           # fees are actually charged


def test_close_futures_short_pnl_sign():
    r = _flat_runner(entry=24000.0, side=Side.SELL)
    r._close_futures(23950.0, "managed")
    assert r.recorded[0][6] > 0               # a short that fell is a winner


def test_close_futures_is_idempotent():
    r = _flat_runner()
    r._close_futures(24050.0, "eod")
    r._close_futures(24050.0, "eod")
    assert len(r.recorded) == 1


def test_fill_is_never_the_signal_price():
    """The whole point: the booked entry must be a price the market had."""
    r = _runner()
    bar = _bar(datetime.now(IST))
    ack = OrderAck("DRY", "i", OrderStatus.NEW, message="dry_run")
    fill = r._fill_price(ack, bar, Side.BUY)
    assert fill != bar.close + 100     # a stale OTE level 100 pts away
    assert abs(fill - bar.close) <= 1.0
