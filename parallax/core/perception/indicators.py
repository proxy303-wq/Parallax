"""Deterministic, vectorized indicator computations.

Pure functions of price/volume arrays, vectorized with pandas/numpy.  Warm-up
positions are reported as NaN so downstream layers can distinguish "not
enough data" from a genuine zero.  No state, no I/O, no look-ahead.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(values, period: int) -> np.ndarray:
    v = np.asarray(values, dtype=float)
    out = np.full(len(v), np.nan)
    if len(v) >= period:
        csum = np.cumsum(np.insert(v, 0, 0.0))
        out[period - 1:] = (csum[period:] - csum[:-period]) / period
    return out


def ema(values, period: int) -> np.ndarray:
    s = pd.Series(values).ewm(span=period, adjust=False).mean()
    out = s.to_numpy()
    if len(out) >= period:
        out[:period - 1] = np.nan
    return out


def rsi(close, period: int = 14) -> np.ndarray:
    c = pd.Series(np.asarray(close, dtype=float))
    delta = c.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(avg_loss != 0, 100.0)
    out.iloc[:period] = np.nan
    return out.to_numpy()


def true_range(high, low, close) -> np.ndarray:
    h = pd.Series(np.asarray(high, dtype=float))
    l = pd.Series(np.asarray(low, dtype=float))
    c = pd.Series(np.asarray(close, dtype=float))
    prev_close = c.shift(1)
    tr = pd.concat([h - l, (h - prev_close).abs(), (l - prev_close).abs()],
                   axis=1).max(axis=1)
    out = tr.to_numpy()
    if len(out):
        out[0] = float(h.iloc[0] - l.iloc[0])
    return out


def _wilder_smooth(x: np.ndarray, period: int) -> np.ndarray:
    s = pd.Series(x).ewm(alpha=1.0 / period, adjust=False).mean()
    out = s.to_numpy()
    if len(out) >= period:
        out[:period - 1] = np.nan
    return out


def atr(high, low, close, period: int = 14) -> np.ndarray:
    tr = true_range(high, low, close)
    return _wilder_smooth(np.nan_to_num(tr, nan=0.0), period)


def adx(high, low, close, period: int = 14):
    """Return (adx, plus_di, minus_di) series (Wilder)."""
    h = pd.Series(np.asarray(high, dtype=float))
    l = pd.Series(np.asarray(low, dtype=float))
    up = h.diff()
    down = -l.diff()
    plus_dm = ((up > down) & (up > 0)) * up
    minus_dm = ((down > up) & (down > 0)) * down
    tr = pd.Series(true_range(high, low, close))
    atr_s = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_s
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_s
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx_ = dx.ewm(alpha=1.0 / period, adjust=False).mean()
    for s in (plus_di, minus_di):
        s.iloc[:period] = np.nan
    adx_.iloc[:period * 2 - 1] = np.nan
    return adx_.to_numpy(), plus_di.to_numpy(), minus_di.to_numpy()


def rolling_high(high, period: int) -> np.ndarray:
    return pd.Series(high).rolling(period).max().to_numpy()


def rolling_low(low, period: int) -> np.ndarray:
    return pd.Series(low).rolling(period).min().to_numpy()


def vwap(high, low, close, volume) -> np.ndarray:
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    v = np.asarray(volume, dtype=float)
    typical = (h + l + c) / 3.0
    pv = typical * v
    cum_pv = np.cumsum(pv)
    cum_v = np.cumsum(v)
    out = np.full(len(c), np.nan)
    nz = cum_v > 0
    out[nz] = cum_pv[nz] / cum_v[nz]
    return out


def realized_vol(close, period: int = 20, periods_per_year: float = 252.0) -> float:
    c = np.asarray(close, dtype=float)
    if len(c) < period + 1:
        return float("nan")
    rets = np.diff(np.log(c[-(period + 1):]))
    return float(np.std(rets, ddof=1) * np.sqrt(periods_per_year))


def rolling_realized_vol(close, period: int = 20,
                         periods_per_year: float = 252.0) -> np.ndarray:
    c = pd.Series(np.asarray(close, dtype=float))
    log_ret = np.log(c.where(c > 0)).diff()
    rv = log_ret.rolling(period).std(ddof=1) * np.sqrt(periods_per_year)
    return rv.to_numpy()


def precompute(closes, highs, lows, volumes,
                bpy: float = 18900.0) -> dict[str, np.ndarray]:
    """Compute every indicator series once for a full dataset (vectorized).

    The perception builder accepts this dictionary and reads the last value
    of each series, which makes backtests O(1) per bar for indicators.
    """
    c = np.asarray(closes, dtype=float)
    h = np.asarray(highs, dtype=float)
    l = np.asarray(lows, dtype=float)
    v = np.asarray(volumes, dtype=float)
    adx_s, plus_di, minus_di = adx(h, l, c, 14)
    return {
        "sma20": sma(c, 20),
        "sma50": sma(c, 50),
        "ema_fast": ema(c, 9),
        "ema_slow": ema(c, 21),
        "ema200": ema(c, 200),
        "rsi": rsi(c, 14),
        "atr": atr(h, l, c, 14),
        "vwap": vwap(h, l, c, v),
        "adx": adx_s,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "rv": rolling_realized_vol(c, 20, bpy),
    }


def relative_volume(volume, period: int = 20) -> float:
    v = np.asarray(volume, dtype=float)
    if len(v) == 0:
        return float("nan")
    if len(v) == 1:
        return 1.0
    base = v[:-1][-period:]
    avg = base.mean()
    if avg == 0:
        return float("nan")
    return float(v[-1] / avg)


def rolling_percentile_rank(values, period: int = 100) -> float:
    v = np.asarray(values, dtype=float)
    if len(v) == 0:
        return float("nan")
    win = v[-period:]
    cur = v[-1]
    return float((win < cur).mean())
