"""Hypothesis, evidence, and edge tests."""
from parallax.contracts import Direction, MarketState
from parallax.core.hypotheses import HypothesisEngine
from parallax.core.reasoning import expected_value, sigmoid, softmax

from conftest import make_state


def test_hypotheses_always_include_no_trade():
    state = make_state()
    hyps = HypothesisEngine().generate(state)
    ids = {h.id for h in hyps}
    assert "H5_NO_TRADE" in ids
    assert any(h.direction == Direction.LONG for h in hyps)
    assert any(h.direction == Direction.SHORT for h in hyps)
    assert all(0.0 <= h.confidence <= 1.0 for h in hyps)


def test_softmax_normalizes():
    p = softmax([2.0, -2.0, 1.0])
    assert abs(sum(p) - 1.0) < 1e-9


def test_sigmoid_monotonic():
    assert sigmoid(1) > sigmoid(0) > sigmoid(-1)


def test_expected_value_break_even():
    # p = 1/3, RR = 2 => EV = 0
    assert abs(expected_value(1 / 3, 2.0)) < 1e-9
    assert expected_value(0.6, 2.0) > 0
    assert expected_value(0.2, 2.0) < 0


def test_bullish_state_prefers_long():
    from conftest import make_bars
    state = make_state(bars=make_bars(seed=3, drift=2.5))
    hyps = HypothesisEngine().generate(state)
    long = next(h for h in hyps if h.direction == Direction.LONG)
    short = next(h for h in hyps if h.direction == Direction.SHORT)
    assert long.confidence > short.confidence
