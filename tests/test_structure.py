"""SMC structure-detection tests."""
from datetime import datetime, timedelta, timezone

from parallax.contracts import Bar, SwingKind
from parallax.core.perception.structure import (
    detect_breaks, detect_fvg, detect_liquidity, detect_sweep, detect_swings,
)


def _bars(closes):
    ts = datetime(2026, 9, 18, 9, 15, tzinfo=timezone.utc)
    bars = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        bars.append(Bar(ts + timedelta(minutes=5 * i), o, c + 1, c - 1, c, 100))
    return bars


def test_detect_swings_finds_pivots():
    # zig-zag: clear alternating highs/lows
    closes = [10, 9, 8, 9, 10, 11, 10, 9, 8, 9, 10, 11, 12, 11, 10, 9, 8, 9, 10, 11]
    bars = _bars(closes)
    swings = detect_swings(bars, left=2, right=2)
    kinds = [s.kind for s in swings]
    assert SwingKind.HIGH in kinds and SwingKind.LOW in kinds
    # every swing is confirmed within the series
    for s in swings:
        assert s.known_idx < len(bars)


def test_detect_breaks_uptrend_bos():
    closes = [10, 11, 12, 11, 12, 13, 12, 13, 14, 13, 14, 15, 14, 15, 16, 15, 16, 17, 16, 17]
    bars = _bars(closes)
    swings = detect_swings(bars, left=2, right=2)
    breaks = detect_breaks(bars, swings)
    assert len(breaks) > 0
    assert breaks[-1].direction == "bullish"


def test_detect_fvg_finds_gap():
    closes = [10, 10, 10, 14, 14]  # bar 3 low > bar 1 high
    bars = _bars(closes)
    zones = detect_fvg(bars)
    assert len(zones) > 0
    assert zones[0].bottom < zones[0].top


def test_detect_liquidity_returns_levels():
    closes = [10, 9, 8, 9, 10, 9, 8, 9, 10, 9, 8, 9, 10, 9, 8, 9, 10, 9, 8]
    bars = _bars(closes)
    swings = detect_swings(bars, left=2, right=2)
    levels = detect_liquidity(bars, swings)
    assert len(levels) > 0


def _tight(closes):
    ts = datetime(2026, 9, 18, 9, 15, tzinfo=timezone.utc)
    return [Bar(ts + timedelta(minutes=5 * i), c, c, c, c, 100)
            for i, c in enumerate(closes)]


def test_detect_sweep_sell_side():
    # price dips below 10 then closes back above -> sell-side sweep (bullish)
    bars = _tight([11, 10.5, 9.5, 9.0, 9.8, 10.2, 10.6])
    assert detect_sweep(bars, 10.0, "sell", lookback=10)


def test_detect_sweep_no_sweep():
    bars = _tight([11, 10.5, 10.2, 10.4, 10.6, 10.8, 11.0])
    assert not detect_sweep(bars, 10.0, "sell", lookback=10)


def test_detect_sweep_buy_side():
    # price pops above 11 then closes back below -> buy-side sweep (bearish)
    bars = _tight([10, 10.4, 11.5, 11.8, 11.2, 10.8, 10.6])
    assert detect_sweep(bars, 11.0, "buy", lookback=10)
