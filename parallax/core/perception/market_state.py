"""Build a MarketState from a list of closed OHLCV bars.

This is the single structured snapshot every downstream layer consumes.  It
runs the SMC structure engine, liquidity detection, session/anchor levels and
data-quality checks.  Indicators are either computed here or supplied as
precomputed series (for fast backtests).  Regime classification is left to
the context layer.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from parallax.contracts import (
    DataQuality, DataQualityState, IndicatorState, InstrumentId, LiquidityState,
    MarketState, PriceState, SessionState, StructureState, TimeframeState,
    VolatilityState, VolumeState,
)
from parallax.contracts.structure import Bar, LiquiditySide

from . import indicators as ind
from . import structure as struct

_BARS_PER_YEAR = {
    "1m": 375 * 252, "5m": 75 * 252, "15m": 25 * 252, "30m": 13 * 252,
    "1h": 7 * 252, "4h": 2 * 252, "1d": 252,
}


def bars_per_year(timeframe: str) -> float:
    return _BARS_PER_YEAR.get(timeframe, 252.0)


def _last(series) -> float | None:
    for x in reversed(series):
        if x is None:
            continue
        try:
            if np.isnan(x):
                continue
        except TypeError:
            pass
        return float(x)
    return None


def _session_levels(bars: list[Bar]) -> tuple:
    if not bars:
        return None, None, None, None, None
    last_date = bars[-1].ts.date()
    today = [b for b in bars if b.ts.date() == last_date]
    prev_days = [b for b in bars if b.ts.date() < last_date]
    session_open = today[0].open if today else bars[0].open
    session_high = max((b.high for b in today), default=None)
    session_low = min((b.low for b in today), default=None)
    prev_day_high = None
    prev_day_low = None
    if prev_days:
        prev_date = prev_days[-1].ts.date()
        pd = [b for b in prev_days if b.ts.date() == prev_date]
        prev_day_high = max((b.high for b in pd), default=None)
        prev_day_low = min((b.low for b in pd), default=None)
    return session_open, session_high, session_low, prev_day_high, prev_day_low


def _opening_range(bars: list[Bar], n: int = 3) -> tuple:
    """(high, low) of the first 'n' bars of the current session — the
    session-local pool of stops the corpus identified as the right liquidity
    reference (not PDH/PDL)."""
    if not bars:
        return None, None
    last_date = bars[-1].ts.date()
    today = [b for b in bars if b.ts.date() == last_date]
    first = today[:n]
    if not first:
        return None, None
    return max(b.high for b in first), min(b.low for b in first)


def _check_data_quality(bars: list[Bar], now: datetime | None = None) -> DataQualityState:
    dq = DataQualityState()
    dq.last_bar_ts = bars[-1].ts if bars else None
    if not bars:
        dq.status = DataQuality.MISSING
        dq.issues.append("no bars")
        return dq

    seen: set[datetime] = set()
    dupes = 0
    prev = None
    discontinuities = 0
    for b in bars:
        if b.ts in seen:
            dupes += 1
        seen.add(b.ts)
        if prev is not None and b.ts <= prev:
            discontinuities += 1
        prev = b.ts
    dq.duplicate_bars = dupes
    dq.timestamp_discontinuities = discontinuities
    if dupes:
        dq.issues.append(f"{dupes} duplicate bars")
    if discontinuities:
        dq.issues.append(f"{discontinuities} non-monotonic timestamps")

    now = now or datetime.now(timezone.utc)
    if bars[-1].ts.tzinfo is not None:
        dq.staleness_seconds = (now - bars[-1].ts).total_seconds()

    dq.status = DataQuality.INVALID if dq.issues else DataQuality.GOOD
    return dq


def build_market_state(instrument: InstrumentId, bars: list[Bar],
                       timeframe: str = "5m",
                       lookback: int | None = None,
                       now: datetime | None = None,
                       series: dict[str, np.ndarray] | None = None) -> MarketState:
    """Assemble a MarketState.  Bars sorted ascending and closed.

    'series' optionally supplies precomputed indicator arrays aligned to
    'bars' (same length) — the builder then reads the last value of each.
    """
    if not bars:
        raise ValueError("build_market_state requires at least one bar")
    if lookback is not None:
        bars = bars[-lookback:]
    n = len(bars)
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    volumes = [b.volume for b in bars]
    last = bars[-1]

    vals: dict[str, float] = {}
    if series is not None:
        for k in ("sma20", "sma50", "ema_fast", "ema_slow", "ema200",
                  "rsi", "atr", "vwap", "adx", "plus_di", "minus_di"):
            arr = series.get(k)
            if arr is not None and len(arr):
                v = arr[-1]
                if not np.isnan(v):
                    vals[k] = float(v)
        rv_series = series.get("rv", np.array([]))
    else:
        for name, s in [
            ("sma20", ind.sma(closes, 20)),
            ("sma50", ind.sma(closes, 50)),
            ("ema_fast", ind.ema(closes, 9)),
            ("ema_slow", ind.ema(closes, 21)),
            ("ema200", ind.ema(closes, 200)),
            ("rsi", ind.rsi(closes, 14)),
            ("atr", ind.atr(highs, lows, closes, 14)),
            ("vwap", ind.vwap(highs, lows, closes, volumes)),
        ]:
            v = _last(s)
            if v is not None:
                vals[name] = v
        adx_s, plus_di, minus_di = ind.adx(highs, lows, closes, 14)
        for name, s in [("adx", adx_s), ("plus_di", plus_di), ("minus_di", minus_di)]:
            v = _last(s)
            if v is not None:
                vals[name] = v
        rv_series = ind.rolling_realized_vol(closes, 20, bars_per_year(timeframe))

    indicators = IndicatorState(values=vals)

    rv = _last(rv_series) if len(rv_series) else None
    vol_pct = None
    if rv is not None and len(rv_series) > 1:
        # trailing-window percentile (point-in-time correct: a live system sees
        # recent history, never the full future distribution)
        win = rv_series[-100:]
        valid = win[~np.isnan(win)]
        if len(valid) > 1:
            vol_pct = float((valid < rv).mean())

    atr_v = vals.get("atr")
    vol = VolatilityState(
        atr=atr_v,
        atr_pct=(atr_v / last.close) if atr_v else None,
        realized_vol=rv if rv is not None else None,
        vol_percentile=vol_pct,
    )

    swings = struct.detect_swings(bars)
    breaks = struct.detect_breaks(bars, swings)
    zones = struct.detect_order_blocks(bars, breaks) + struct.detect_fvg(bars)
    liquidity = struct.detect_liquidity(bars, swings)

    trend = "NEUTRAL"
    if breaks:
        trend = "BULLISH" if breaks[-1].direction == "bullish" else "BEARISH"

    o_high, o_low = _opening_range(bars)
    structure_state = StructureState(
        trend=trend, bias=trend, swings=swings, breaks=breaks, zones=zones,
        last_break=breaks[-1] if breaks else None,
        last_mss=next((b for b in reversed(breaks) if b.kind.value == "CHoCH"), None),
        swept_opening_low=struct.detect_sweep(bars, o_low, "sell"),
        swept_opening_high=struct.detect_sweep(bars, o_high, "buy"),
    )

    price = last.close
    buy_levels = [lv for lv in liquidity if lv.side == LiquiditySide.BUY and lv.price > price]
    sell_levels = [lv for lv in liquidity if lv.side == LiquiditySide.SELL and lv.price < price]
    liq_state = LiquidityState(
        levels=liquidity,
        nearest_buy_side=min((lv.price for lv in buy_levels), default=None),
        nearest_sell_side=max((lv.price for lv in sell_levels), default=None),
        equal_highs=[lv.price for lv in liquidity if lv.side == LiquiditySide.BUY and lv.count >= 2],
        equal_lows=[lv.price for lv in liquidity if lv.side == LiquiditySide.SELL and lv.count >= 2],
    )

    avg_vol = float(np.mean(volumes[-20:])) if n else None
    rel_vol = (last.volume / avg_vol) if avg_vol else None
    short_avg = float(np.mean(volumes[-5:])) if n else 0.0
    long_avg = float(np.mean(volumes[-20:])) if n else 0.0
    vol_state = VolumeState(
        volume=last.volume, relative_volume=rel_vol, avg_volume=avg_vol,
        volume_trend=0.0 if long_avg == 0 else (short_avg - long_avg) / long_avg,
    )

    prev_close = closes[-2] if n > 1 else None
    s_open, s_high, s_low, pdh, pdl = _session_levels(bars)
    o_high, o_low = _opening_range(bars)
    price_state = PriceState(
        last=last.close, bid=last.close, ask=last.close, prev_close=prev_close,
        open_today=s_open, high_today=s_high, low_today=s_low,
    )

    session = SessionState(
        name="OPEN" if s_high is not None else "CLOSED", is_open=True,
        session_high=s_high, session_low=s_low, prev_day_high=pdh,
        prev_day_low=pdl, session_open=s_open,
        opening_high=o_high, opening_low=o_low,
    )

    dq = _check_data_quality(bars, now)

    tf_state = TimeframeState(timeframe=timeframe, bars=bars, last=last,
                              high=max(highs), low=min(lows))

    return MarketState(
        instrument=instrument, timestamp=last.ts, session=session,
        timeframes={timeframe: tf_state}, price=price_state, volume=vol_state,
        volatility=vol, structure=structure_state, liquidity=liq_state,
        indicators=indicators, data_quality=dq,
    )
