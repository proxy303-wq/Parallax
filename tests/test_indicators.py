"""Deterministic indicator tests (§27)."""
import numpy as np

from parallax.core.perception.indicators import (
    adx, atr, ema, rsi, sma, true_range,
)


def test_sma():
    out = sma([1, 2, 3, 4, 5], 3)
    assert np.isnan(out[0]) and np.isnan(out[1])
    assert out[2] == 2.0 and out[3] == 3.0 and out[4] == 4.0


def test_ema_rising():
    out = ema(np.arange(1, 101, dtype=float), 9)
    assert out[-1] > 50  # tracks the rising series


def test_rsi_rising_is_100():
    out = rsi(np.arange(1, 40, dtype=float), 14)
    assert out[-1] == 100.0


def test_rsi_falling_is_0():
    out = rsi(np.arange(40, 1, -1, dtype=float), 14)
    assert out[-1] == 0.0


def test_true_range():
    tr = true_range([10, 11], [9, 9], [10, 10])
    assert tr[0] == 1.0
    assert tr[1] == max(11 - 9, abs(11 - 10), abs(9 - 10)) == 2.0


def test_atr_constant_range():
    highs = [10 + i for i in range(30)]
    lows = [8 + i for i in range(30)]
    closes = [9 + i for i in range(30)]
    out = atr(highs, lows, closes, 14)
    assert abs(out[-1] - 2.0) < 0.01


def test_adx_bounds_and_length():
    highs = [10 + i for i in range(60)]
    lows = [8 + i for i in range(60)]
    closes = [9 + i for i in range(60)]
    a, p, m = adx(highs, lows, closes, 14)
    assert len(a) == 60
    assert 0 <= a[-1] <= 100
