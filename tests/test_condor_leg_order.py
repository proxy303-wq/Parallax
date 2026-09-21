"""The 0DTE condor must buy its hedges before it sells the shorts.

Sent short-first, each short is naked when it arrives and Dhan margins it as
such: 8 lots of naked ATM-2 NIFTY put is ~Rs 13.1 lakh, two of them ~Rs 26.2
lakh.  An Rs 8L account cannot post that, so the condor would silently never
open.  Hedges-first lets SPAN net the spread down to the defined-risk ceiling.
"""
import types

from parallax.apps.worker.dhan_options_live import ZeroDteCondor


def _trader(record):
    t = object.__new__(ZeroDteCondor)
    t.lots = 8
    t.broker = types.SimpleNamespace(
        dry_run=True,
        place_option_order=lambda c, side, lots, ot: record.append(
            (c.strike, side)) or types.SimpleNamespace(status="DRY"))
    t.instrument_name = "NIFTY 0DTE"
    t.hold_to_expiry = False
    t._say = lambda m: None
    t.journal = types.SimpleNamespace(set_position=lambda *a: None,
                                      clear_positions=lambda: None,
                                      clear_position=lambda *a: None,
                                      record_trade=lambda *a, **k: None)
    t.telegram = types.SimpleNamespace(configured=False, send=lambda m: None)
    t._start_feed = lambda plan: None
    t._stop_feed = lambda: None
    t.feed = None
    t.active = None
    t.last_value = 0.0
    t.last_pnl = 0.0
    t.peak_pct = 0.0
    return t


def _plan(atm=23350.0):
    legs = {
        "put_short": {"strike": atm - 100, "type": "PE", "security_id": 1},
        "call_short": {"strike": atm + 100, "type": "CE", "security_id": 2},
        "put_hedge": {"strike": atm - 200, "type": "PE", "security_id": 3},
        "call_hedge": {"strike": atm + 200, "type": "CE", "security_id": 4},
    }
    return {"atm": atm, "credit": 20.0, "iv": 0.2, "realized": 0.15, "legs": legs}


def test_hedges_are_bought_before_shorts_are_sold():
    rec = []
    _trader(rec).enter(_plan())
    sides = [s for _k, s in rec]
    assert sides == ["BUY", "BUY", "SELL", "SELL"], rec


def test_shorts_never_precede_a_hedge_on_their_own_side():
    rec = []
    _trader(rec).enter(_plan(24000.0))
    strikes = [k for k, _s in rec]
    # put hedge (ATM-200) before put short (ATM-100); call hedge before call short
    assert strikes.index(24000 - 200) < strikes.index(24000 - 100)
    assert strikes.index(24000 + 200) < strikes.index(24000 + 100)


def test_all_four_legs_are_sent_exactly_once():
    rec = []
    _trader(rec).enter(_plan())
    assert len(rec) == 4
    assert len({k for k, _s in rec}) == 4


def test_rejected_short_unwinds_the_hedges():
    """No basket API, so a mid-entry rejection must not leave a half position."""
    rec = []
    said = []
    t = _trader(rec)
    t._say = lambda m: said.append(m)

    class Rej:
        status = "REJECTED"
        message = "insufficient margin"

    calls = {"n": 0}
    orig = t.broker.place_option_order

    def place(c, side, lots, ot):
        calls["n"] += 1
        if calls["n"] == 3:               # the first short
            return Rej()
        return orig(c, side, lots, ot)

    t.broker.place_option_order = place
    t.enter(_plan())
    assert t.active is None                       # never opened
    assert any("ENTER FAILED" in m for m in said)
    # both hedges were unwound, so 2 buy + 2 sell-back
    assert [s for _k, s in rec] == ["BUY", "BUY", "SELL", "SELL"]


def test_rejected_hedge_unwinds_nothing_extra():
    rec = []
    said = []
    t = _trader(rec)
    t._say = lambda m: said.append(m)

    class Rej:
        status = "REJECTED"
        message = "no funds"

    def place(c, side, lots, ot):
        return Rej()

    t.broker.place_option_order = place
    t.enter(_plan())
    assert t.active is None
    assert rec == []                              # nothing went through
