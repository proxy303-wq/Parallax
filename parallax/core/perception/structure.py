"""Deterministic SMC market-structure detection.

Swings, breaks of structure (BOS / CHoCH), fair-value gaps, order blocks and
liquidity levels.  Every emitted object carries a 'known_idx'/'known_ts' — the
earliest moment a live system could have seen it — so the whole stack is
replay-safe and look-ahead-free.
"""
from __future__ import annotations

from parallax.contracts.structure import (
    Bar, Break, BreakKind, LiquidityLevel, LiquiditySide, Swing, SwingKind,
    Zone, ZoneKind,
)


def detect_swings(bars: list[Bar], left: int = 3, right: int = 3) -> list[Swing]:
    """Detect fractal pivots.  A swing at index i is confirmable only at i+right."""
    swings: list[Swing] = []
    n = len(bars)
    for i in range(left, n - right):
        hi = bars[i].high
        lo = bars[i].low
        is_high = all(hi >= bars[j].high for j in range(i - left, i + right + 1))
        is_low = all(lo <= bars[j].low for j in range(i - left, i + right + 1))
        if is_high and is_low:
            continue  # flat window — no meaningful pivot
        if is_high:
            swings.append(Swing(SwingKind.HIGH, hi, i, i + right,
                                bars[i].ts, bars[i + right].ts))
        elif is_low:
            swings.append(Swing(SwingKind.LOW, lo, i, i + right,
                                bars[i].ts, bars[i + right].ts))
    return swings


def detect_breaks(bars: list[Bar], swings: list[Swing]) -> list[Break]:
    """Classify swing breaks as BOS (continuation) or CHoCH (reversal).

    A break above the prior swing high while bearish is a CHoCH (trend flip to
    bullish); while bullish/neutral it is a BOS.  Symmetric on the downside.
    """
    breaks: list[Break] = []
    trend = "NEUTRAL"
    last_high: Swing | None = None
    last_low: Swing | None = None
    for s in swings:
        if s.kind == SwingKind.HIGH:
            if last_high is not None and s.price > last_high.price:
                if trend == "BEARISH":
                    breaks.append(Break(BreakKind.CHOCH, "bullish", s.price, s.idx,
                                        s.known_idx, s.ts, s.known_ts, last_high.price))
                    trend = "BULLISH"
                else:
                    breaks.append(Break(BreakKind.BOS, "bullish", s.price, s.idx,
                                        s.known_idx, s.ts, s.known_ts, last_high.price))
                    trend = "BULLISH"
            last_high = s
        else:
            if last_low is not None and s.price < last_low.price:
                if trend == "BULLISH":
                    breaks.append(Break(BreakKind.CHOCH, "bearish", s.price, s.idx,
                                        s.known_idx, s.ts, s.known_ts, last_low.price))
                    trend = "BEARISH"
                else:
                    breaks.append(Break(BreakKind.BOS, "bearish", s.price, s.idx,
                                        s.known_idx, s.ts, s.known_ts, last_low.price))
                    trend = "BEARISH"
            last_low = s
    return breaks


def detect_fvg(bars: list[Bar]) -> list[Zone]:
    """Three-candle fair-value gaps (imbalance)."""
    zones: list[Zone] = []
    n = len(bars)
    for i in range(2, n):
        if bars[i].low > bars[i - 2].high:
            zones.append(Zone(ZoneKind.FVG, "bullish", bars[i].low, bars[i - 2].high,
                              i, i, bars[i].ts, bars[i].ts))
        elif bars[i].high < bars[i - 2].low:
            zones.append(Zone(ZoneKind.FVG, "bearish", bars[i - 2].low, bars[i].high,
                              i, i, bars[i].ts, bars[i].ts))
    return zones


def detect_order_blocks(bars: list[Bar], breaks: list[Break],
                        lookback: int = 6) -> list[Zone]:
    """The last opposing candle before a displacement (break) is the order block.

    known_idx is the break's known_idx: the candle only becomes an OB once the
    displacement that reveals it has been confirmed.
    """
    zones: list[Zone] = []
    for b in breaks:
        idx = b.idx
        for j in range(idx - 1, max(idx - lookback, -1), -1):
            if b.direction == "bullish" and not bars[j].bullish:
                zones.append(Zone(ZoneKind.OB, "bullish", bars[j].high, bars[j].low,
                                  j, b.known_idx, bars[j].ts, b.known_ts))
                break
            if b.direction == "bearish" and bars[j].bullish:
                zones.append(Zone(ZoneKind.OB, "bearish", bars[j].high, bars[j].low,
                                  j, b.known_idx, bars[j].ts, b.known_ts))
                break
    return zones


def _cluster(swings: list[Swing], side: LiquiditySide, tol: float,
             max_gap: int) -> list[LiquidityLevel]:
    """Merge nearby equal highs/lows into single liquidity levels."""
    levels: list[LiquidityLevel] = []
    if not swings:
        return levels
    cur_price = swings[0].price
    cur_idx = swings[0].idx
    cur_known = swings[0].known_idx
    cur_ts = swings[0].ts
    cur_known_ts = swings[0].known_ts
    count = 1
    for s in swings[1:]:
        if abs(s.price - cur_price) <= tol * max(cur_price, 1e-9) and (s.idx - cur_idx) <= max_gap:
            count += 1
            cur_idx = s.idx
            cur_known = s.known_idx
            cur_ts = s.ts
            cur_known_ts = s.known_ts
        else:
            levels.append(LiquidityLevel(side, cur_price, cur_idx, cur_known,
                                         cur_ts, cur_known_ts, False, count))
            cur_price = s.price
            cur_idx = s.idx
            cur_known = s.known_idx
            cur_ts = s.ts
            cur_known_ts = s.known_ts
            count = 1
    levels.append(LiquidityLevel(side, cur_price, cur_idx, cur_known,
                                 cur_ts, cur_known_ts, False, count))
    return levels


def detect_liquidity(bars: list[Bar], swings: list[Swing],
                     tol: float = 0.0004, max_gap: int = 8) -> list[LiquidityLevel]:
    """Buy-side liquidity from swing highs, sell-side from swing lows."""
    highs = [s for s in swings if s.kind == SwingKind.HIGH]
    lows = [s for s in swings if s.kind == SwingKind.LOW]
    levels = _cluster(highs, LiquiditySide.BUY, tol, max_gap)
    levels += _cluster(lows, LiquiditySide.SELL, tol, max_gap)
    return levels


def detect_sweep(bars: list[Bar], level: float | None, side: str,
                 lookback: int = 20) -> bool:
    """True when price recently swept a level and reclaimed the opposite side.

    side='sell' -> price traded *below* the level (running sell-side stops) and
    has since closed back *above* it (the bullish sweep-and-reverse).
    side='buy'  -> price traded *above* the level and closed back below it.

    This is the session-local liquidity mechanism (opening range) that replaces
    the daily/weekly high-low substitute the corpus found to be worse-than-random.
    """
    if level is None or not bars:
        return False
    window = bars[-lookback:] if len(bars) > lookback else bars
    last_close = window[-1].close
    if side == "sell":
        return any(b.low < level for b in window) and last_close > level
    if side == "buy":
        return any(b.high > level for b in window) and last_close < level
    return False
