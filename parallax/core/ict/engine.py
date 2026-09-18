"""ICT/SMC entry engine — the Zero→Hero master sequence, deterministic.

Master sequence (bullish):
  1. HTF bias bullish
  2. obvious sell-side liquidity (SSL) is swept (taken below + reclaimed)
  3. bullish displacement (large body, close near the top of the candle)
  4. MSS — displacement closes above the last meaningful lower high
  5. a bullish FVG is created during the displacement
  6. price retraces into the FVG (entry trigger)
  7. entry = FVG CE, SL = below the sweep, DOL = next buy-side liquidity

Every step is a numeric rule (playbook §23) so the model is reproducible and
look-ahead-free (playbook §24/§28).
"""
from __future__ import annotations

from parallax.contracts import Bar, MarketState, SwingKind
from parallax.core.perception.structure import detect_fvg

from .types import ICTConfig, ICTSignal


def detect(state: MarketState, bars: list[Bar],
           config: ICTConfig | None = None) -> ICTSignal | None:
    config = config or ICTConfig()
    atr = state.indicators.atr or 0.0
    if atr <= 0:
        return None
    long = _detect(state, bars, config, atr, "long")
    short = _detect(state, bars, config, atr, "short")
    if long and short:
        # prefer the setup whose sweep is more recent
        return long if long.as_dict()["sweep_extreme"] >= short.as_dict()["sweep_extreme"] else short
    return long or short


def _htf_bias(state: MarketState) -> str:
    ind = state.indicators
    price = state.last_price or 0.0
    sma20 = ind.get("sma20", 0.0)
    sma50 = ind.get("sma50", 0.0)
    if sma50 and sma20 > sma50 and price > sma50:
        return "bullish"
    if sma50 and sma20 < sma50 and price < sma50:
        return "bearish"
    return "neutral"


def _sweep_level(state: MarketState, direction: str) -> float | None:
    price = state.last_price or 0.0
    if direction == "long":
        for lvl in (state.liquidity.nearest_sell_side, state.session.prev_day_low,
                    state.session.session_low):
            if lvl is not None and lvl < price:
                return lvl
    else:
        for lvl in (state.liquidity.nearest_buy_side, state.session.prev_day_high,
                    state.session.session_high):
            if lvl is not None and lvl > price:
                return lvl
    return None


def _find_sweep(bars: list[Bar], level: float, direction: str,
                config: ICTConfig, atr: float):
    n = len(bars)
    start = max(0, n - config.sweep_lookback)
    min_pen = config.sweep_atr * atr
    for i in range(start, n):
        if direction == "long" and (level - bars[i].low) >= min_pen:
            for j in range(i + 1, min(n, i + config.reclaim_bars + 1)):
                if bars[j].close > level:
                    sweep_low = min(b.low for b in bars[i:j + 1])
                    return j, sweep_low
        elif direction == "short" and (bars[i].high - level) >= min_pen:
            for j in range(i + 1, min(n, i + config.reclaim_bars + 1)):
                if bars[j].close < level:
                    sweep_high = max(b.high for b in bars[i:j + 1])
                    return j, sweep_high
    return None


def _find_displacement(bars: list[Bar], after_idx: int, direction: str,
                       config: ICTConfig) -> int | None:
    n = len(bars)
    base = [abs(b.close - b.open) / (b.high - b.low or 1e-9)
            for b in bars[max(0, after_idx - 20):after_idx]]
    for i in range(after_idx, min(n, after_idx + 12)):
        bar = bars[i]
        rng = bar.high - bar.low
        if rng <= 0:
            continue
        body_ratio = abs(bar.close - bar.open) / rng
        pct = sum(1 for x in base if x < body_ratio) / max(1, len(base))
        if pct < config.displacement_percentile:
            continue
        if direction == "long" and bar.close > bar.open and                 (bar.close - bar.low) / rng >= config.close_location:
            return i
        if direction == "short" and bar.close < bar.open and                 (bar.high - bar.close) / rng >= config.close_location:
            return i
    return None


def _mss(bars: list[Bar], state: MarketState, disp_idx: int, direction: str,
        window: int = 6) -> bool:
    swings = state.structure.swings
    n = len(bars)
    if direction == "long":
        highs = [s for s in swings if s.kind == SwingKind.HIGH and s.idx < disp_idx]
        if not highs:
            return False
        last = max(highs, key=lambda s: s.idx)
        return any(bars[j].close > last.price
                   for j in range(disp_idx, min(n, disp_idx + window)))
    lows = [s for s in swings if s.kind == SwingKind.LOW and s.idx < disp_idx]
    if not lows:
        return False
    last = max(lows, key=lambda s: s.idx)
    return any(bars[j].close < last.price
               for j in range(disp_idx, min(n, disp_idx + window)))


def _find_fvg(bars: list[Bar], disp_idx: int, direction: str,
              config: ICTConfig, atr: float):
    min_gap = config.fvg_min_atr * atr
    for z in detect_fvg(bars):
        if z.direction != direction:
            continue
        if z.idx < disp_idx - 1 or z.idx > disp_idx + 6:
            continue
        if (z.top - z.bottom) >= min_gap:
            return z
    return None


def _retraced_into(bars: list[Bar], fvg, direction: str,
                   config: ICTConfig) -> bool:
    start = fvg.known_idx
    for i in range(start, min(len(bars), start + config.retrace_lookback + 1)):
        if direction == "long" and fvg.bottom <= bars[i].low <= fvg.top:
            return True
        if direction == "short" and fvg.bottom <= bars[i].high <= fvg.top:
            return True
    return False


def _dol(state: MarketState, entry: float, direction: str) -> float | None:
    if direction == "long":
        cands = [x for x in (state.liquidity.nearest_buy_side,
                             state.session.prev_day_high, state.session.session_high)
                 if x is not None and x > entry]
        return min(cands) if cands else None
    cands = [x for x in (state.liquidity.nearest_sell_side,
                         state.session.prev_day_low, state.session.session_low)
             if x is not None and x < entry]
    return max(cands) if cands else None


def _detect(state: MarketState, bars: list[Bar], config: ICTConfig,
            atr: float, direction: str) -> ICTSignal | None:
    bias = _htf_bias(state)
    if direction == "long" and bias != "bullish":
        return None
    if direction == "short" and bias != "bearish":
        return None

    level = _sweep_level(state, direction)
    if level is None:
        return None
    sweep = _find_sweep(bars, level, direction, config, atr)
    if sweep is None:
        return None
    sweep_idx, sweep_extreme = sweep

    disp_idx = _find_displacement(bars, sweep_idx, direction, config)
    if disp_idx is None:
        return None

    if not _mss(bars, state, disp_idx, direction):
        return None

    # impulse extreme reached after the displacement + MSS
    n = len(bars)
    if direction == "long":
        impulse_high = max(b.high for b in bars[disp_idx:min(n, disp_idx + 8)])
        impulse = impulse_high - sweep_extreme
        if impulse <= 0:
            return None
        # OTE = 62%–79% retracement of the impulse; entry at the 70.5% midpoint
        ote_top = impulse_high - impulse * config.ote_low      # 0.62 retrace
        ote_bottom = impulse_high - impulse * config.ote_high  # 0.79 retrace
        entry = impulse_high - impulse * 0.705
        # retracement trigger: the limit at the 70.5% OTE midpoint only fills
        # when price actually reaches it, and not if the sweep is re-taken on the same bar
        if bars[-1].low > entry or bars[-1].low <= sweep_extreme:
            return None
        target = _dol(state, entry, direction)
        if target is None or target <= entry:
            return None
        if abs(target - entry) / max(abs(entry - sweep_extreme), 1e-9) < config.min_rr:
            return None
        return ICTSignal(
            direction="long", bias=bias, sweep_level=level,
            sweep_extreme=sweep_extreme, fvg_top=ote_top, fvg_bottom=ote_bottom,
            entry=entry, stop=sweep_extreme, target=target,
            reasons=["HTF bullish", "SSL swept", "displacement", "MSS",
                     "OTE retracement", "DOL"])
    impulse_low = min(b.low for b in bars[disp_idx:min(n, disp_idx + 8)])
    impulse = sweep_extreme - impulse_low
    if impulse <= 0:
        return None
    ote_top = impulse_low + impulse * config.ote_high      # 0.79 retrace
    ote_bottom = impulse_low + impulse * config.ote_low    # 0.62 retrace
    entry = impulse_low + impulse * 0.705
    if bars[-1].high < entry or bars[-1].high >= sweep_extreme:
        return None
    target = _dol(state, entry, direction)
    if target is None or target >= entry:
        return None
    if abs(target - entry) / max(abs(entry - sweep_extreme), 1e-9) < config.min_rr:
        return None
    return ICTSignal(
        direction="short", bias=bias, sweep_level=level,
        sweep_extreme=sweep_extreme, fvg_top=ote_top, fvg_bottom=ote_bottom,
        entry=entry, stop=sweep_extreme, target=target,
        reasons=["HTF bearish", "BSL swept", "displacement", "MSS",
                 "OTE retracement", "DOL"])
