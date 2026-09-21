"""The condor's mark-to-market sign.

cost-to-close = (buy back the shorts at the ask) - (sell the hedges at the bid).
Negated, a freshly opened condor reads as roughly minus its own credit, which
shows up as ~+200% profit and books about twice the credit into the journal the
instant the trade opens.
"""
import types

from parallax.apps.worker.dhan_options_live import LOT, ZeroDteCondor


def _plan():
    return {"atm": 23400.0, "credit": 39.2, "iv": 0.127, "realized": 0.073,
            "legs": {"put_short": {"strike": 23300, "type": "PE",
                                   "security_id": 1, "bid": 38.95, "ask": 39.00},
                     "call_short": {"strike": 23500, "type": "CE",
                                    "security_id": 2, "bid": 32.25, "ask": 32.35},
                     "put_hedge": {"strike": 23200, "type": "PE",
                                   "security_id": 3, "bid": 19.60, "ask": 19.65},
                     "call_hedge": {"strike": 23600, "type": "CE",
                                    "security_id": 4, "bid": 12.25, "ask": 12.35}}}


def _trader(ltps):
    t = object.__new__(ZeroDteCondor)
    t.lots = 8
    t.active = {"plan": _plan(), "entry_time": None}
    t.last_value = None
    t.last_pnl = 0.0
    t.feed = types.SimpleNamespace(ltp=lambda sid: ltps.get(sid))
    t.journal = types.SimpleNamespace(record_trade=lambda *a, **k: None,
                                      clear_positions=lambda: None)
    t.telegram = types.SimpleNamespace(configured=False, send=lambda m: None)
    t._stop_feed = lambda: None
    return t


def test_a_freshly_opened_condor_is_worth_about_its_credit():
    # feed returns the entry marks, so the cost to close ~ the credit
    ltps = {1: 38.95, 2: 32.25, 3: 19.60, 4: 12.25}
    t = _trader(ltps)
    val = t.value_now()
    assert abs(val - 39.35) < 0.01, val          # shorts - hedges, POSITIVE
    assert abs(t.last_pnl) < 200.0, t.last_pnl   # not a phantom 40k


def test_pnl_is_zero_signed_at_entry():
    ltps = {1: 38.95, 2: 32.25, 3: 19.60, 4: 12.25}
    t = _trader(ltps)
    t.value_now()
    # credit 39.20 vs close cost 39.35 -> a hair negative, never +2x credit
    assert t.last_pnl < 0
    assert abs(t.last_pnl) < 500.0


def test_decay_produces_a_fraction_of_the_credit():
    # everything to zero except a little: profit should approach the credit
    ltps = {1: 5.0, 2: 4.0, 3: 1.0, 4: 0.5}
    t = _trader(ltps)
    val = t.value_now()
    assert abs(val - 7.5) < 0.01
    assert abs(t.last_pnl - (39.2 - 7.5) * LOT * 8) < 1.0


def test_full_decay_caps_at_the_credit():
    ltps = {1: 0.05, 2: 0.05, 3: 0.0, 4: 0.0}
    t = _trader(ltps)
    val = t.value_now()
    t.value_now()
    pnl = (39.2 - val) * LOT * 8
    assert 0 < pnl < 39.2 * LOT * 8          # never more than max profit
