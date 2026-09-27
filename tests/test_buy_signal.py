"""The buying-signal study's causal machinery.

Every one of these tests exists because the failure mode is silent: a lookahead
bug produces a beautiful equity curve and no error.  The two that matter most
are the 15-minute closing rule and the swing confirmation lag - both were wrong
in my first sketch of the plan's rules.
"""
import datetime

import pytest

from parallax.apps.research.buy_signal import (
    IST, build_15m, ema, forward, last_swing_before, session_spans, signals,
    swings, welch)


def synth(flat_sessions=4, px=23000.0, rally=True):
    """Flat sessions, then optionally one that rallies, pauses, and breaks out.

    Two traps this fixture has to avoid, both of which it fell into first:

      * A monotonic rally has NO confirmed swing highs - every bar makes a new
        high - so a breakout can only be tested after a pause.  Hence the
        pullback.
      * A reversal bar opens at the previous close, so with a high drawn as
        max(open, close) + wick its high TIES the peak bar's and no strict local
        maximum exists.  The peak therefore needs a real blow-off wick.
    """
    ts, o, h, l, c, v = [], [], [], [], [], []
    day0 = datetime.datetime(2026, 1, 5, 9, 15, tzinfo=IST)
    price = px
    for d in range(flat_sessions):
        base = day0 + datetime.timedelta(days=d)
        for k in range(75):
            ts.append(int((base + datetime.timedelta(minutes=5 * k)).timestamp()))
            o.append(price); c.append(price)
            h.append(price + 2.0); l.append(price - 2.0); v.append(1000.0)
    if rally:
        base = day0 + datetime.timedelta(days=flat_sessions)
        step = [3.0] * 40 + [-5.0] * 10 + [6.0] * 25
        PEAK = 39
        for k in range(75):
            ts.append(int((base + datetime.timedelta(minutes=5 * k)).timestamp()))
            o.append(price)
            price += step[k]
            c.append(price)
            hi = max(o[-1], price) + 2.0 + (6.0 if k == PEAK else 0.0)
            h.append(hi)
            l.append(min(o[-1], price) - 2.0)
            v.append(2000.0 if k >= 50 else 1000.0)
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c, "v": v}


def test_ema_seeds_on_the_simple_average():
    e = ema([1.0, 2.0, 3.0, 4.0], 3)
    assert e[0] is None and e[1] is None
    assert e[2] == pytest.approx(2.0)
    assert e[3] == pytest.approx(3.0)          # 4*.5 + 2*.5


def test_ema_is_none_until_it_has_enough_samples():
    assert ema([1.0, 2.0], 5) == [None, None]


def test_a_15m_bar_is_not_closed_until_its_third_5m_bar():
    """THE lookahead trap.  At 5m bar 2 the 09:15 15-minute bar is still
    forming; reading it there is reading the future."""
    bars = synth(flat_sessions=1, rally=False)
    spans = session_spans(bars)
    m15, closed = build_15m(bars, spans)
    i0 = spans[0][1]
    assert closed[i0 + 0] == 0        # nothing closed yet
    assert closed[i0 + 1] == 0
    assert closed[i0 + 2] == 0        # the 09:15 bar is still forming at 09:25
    assert closed[i0 + 3] == 1        # 09:30: it has closed
    assert closed[i0 + 5] == 1
    assert closed[i0 + 6] == 2        # 09:45
    assert len(m15) == 25             # 75 five-minute bars


def test_the_15m_bar_aggregates_the_right_three_bars():
    bars = synth(flat_sessions=1, rally=False)
    spans = session_spans(bars)
    m15, _ = build_15m(bars, spans)
    i0 = spans[0][1]
    assert m15[0][1] == max(bars["h"][i0:i0 + 3])
    assert m15[0][2] == min(bars["l"][i0:i0 + 3])
    assert m15[0][3] == bars["c"][i0 + 2]


def test_a_swing_does_not_exist_until_its_right_bars_have_printed():
    bars = synth(flat_sessions=1)
    spans = session_spans(bars)
    sw = swings(bars, spans)
    for at, kind, px in sw:
        assert at >= spans[0][1] + 2
    # the first swing of the rally session is the top before the pullback
    rally_i0 = spans[-1][1]
    tops = [s for s in sw if s[1] == "H" and s[0] >= rally_i0]
    assert tops, "the pullback must leave a confirmed swing high"


def test_last_swing_before_ignores_anything_not_yet_confirmed():
    sw = [(10, "H", 100.0), (20, "H", 200.0), (30, "H", 300.0)]
    assert last_swing_before(sw, 15, "H") == 100.0
    assert last_swing_before(sw, 20, "H") == 200.0
    assert last_swing_before(sw, 9, "H") is None


def test_a_forward_move_never_runs_past_the_bell():
    bars = synth(flat_sessions=1, rally=False)
    spans = session_spans(bars)
    i1 = spans[0][2]
    row = {"i": i1 - 3, "entry": bars["o"][i1 - 2]}
    got = forward(bars, spans, row, 12)
    assert got == bars["c"][i1 - 1] - row["entry"]      # clamped, not past it


def test_welch_is_zero_between_identical_samples():
    a = [1.0, 2.0, 3.0, 4.0]
    d, t = welch(a, list(a))
    assert d == pytest.approx(0.0)
    assert t == pytest.approx(0.0)


def test_welch_finds_a_real_difference():
    d, t = welch([10.0] * 30 + [0.0], [0.0] * 31)
    assert d > 0
    assert t is None or abs(t) > 2


def test_the_breakout_fires_on_the_bar_that_clears_the_pullback_high():
    """End to end: a rally, a pause, then a break of that pause."""
    bars = synth()
    spans = session_spans(bars)
    rows = signals(bars, spans)
    assert rows, "the synthetic session must produce scorable bars"
    rally_i0 = spans[-1][1]
    recent = [r for r in rows if r["i"] >= rally_i0 + 40]
    assert any(r["regime"] == "BULL" for r in recent), "rally is not reading bull"
    breaks = [r for r in recent if r["broke_up"] and r["vol_ok"] and r["exp_ok"]]
    assert breaks, "the break of the pullback high was not detected"
    # and it fires after the swing was confirmable, never before
    tops = [s for s in swings(bars, spans) if s[1] == "H" and s[0] >= rally_i0]
    assert min(b["i"] for b in breaks) > min(t[0] for t in tops)


def test_a_quiet_market_produces_no_breakouts():
    bars = synth(flat_sessions=5, rally=False)
    spans = session_spans(bars)
    rows = signals(bars, spans)
    assert not any(r["broke_up"] or r["broke_dn"] for r in rows)
