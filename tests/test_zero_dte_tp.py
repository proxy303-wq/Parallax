"""Ratchet take-profit ladder for the 0DTE condor.

The ladder is a pure function of the best profit seen, so it is tested
independently of the broker / market feed.
"""
from parallax.apps.worker.dhan_options_live import ZeroDteCondor

floor = ZeroDteCondor.ratchet_floor


def test_no_floor_before_half_credit():
    assert floor(0.0) == 0.0
    assert floor(0.49) == 0.0


def test_floor_arms_at_50():
    assert floor(0.50) == 0.50
    assert floor(0.70) == 0.50
    assert floor(0.79) == 0.50


def test_floor_steps_at_80():
    assert floor(0.80) == 0.75
    assert floor(0.94) == 0.75


def test_floor_steps_at_95():
    assert floor(0.95) == 0.90
    assert floor(1.00) == 0.90
    assert floor(1.20) == 0.90


def test_ladder_is_monotonic():
    prev = -1.0
    for i in range(-50, 151):
        cur = floor(i / 100.0)
        assert cur >= prev
        prev = cur
