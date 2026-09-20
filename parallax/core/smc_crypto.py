"""SMC Fair-Value-Gap retest for BTCUSD - live twin of the validated backtest.

Backtest: the btc_jas_compare research folder (REPORT.md sections 13-17).
This module reproduces its signal logic and keeps live and backtest in step.

CAUSALITY - two separate lags, both found empirically and easy to get wrong:

  1. CONFIRMATION. smc.bos_choch() writes its mark at last_positions[-2], so a BOS at
     bar p is only written once the loop has advanced past the NEXT swing. Measured:
     a value read at output index j first becomes final when the array extends to
     j + swing_length + 1. Reading the last index of a growing prefix therefore never
     sees a BOS at all (measured: 0 fires in 420 bars where the full array has 4).

  2. WINDOW STABILITY. The mark is anchored to the end of the array it was given, so a
     SLIDING window makes the marks chase the window edge and they never become
     readable (measured: 0 signals in 900 bars at every window size 300..6000). The
     fix is a window whose END advances but whose rows are only consumed once CONFIRM
     bars have passed - see SMCCrypto.step().

The SIGNAL LAG defaults to 2 * swing_length. Using swing_length alone reads the BOS one
full swing early (a real look-ahead); measured impact on the backtest was negligible
(return +1822 -> +1850 pct, drawdown -18.95 -> -15.03 pct), so conclusions hold.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from parallax.adapters.smc import smc
from parallax.config.crypto import SMCConfig, CONTRACT_VALUE, MAX_NOTIONAL_USD

# Feature window. Rows are consumed CONFIRM bars behind the window end, so the window
# only has to be long enough for swing detection to be well conditioned.
WINDOW = 400
CONFIRM = 60          # >= swing_length + 1, with margin


# --------------------------------------------------------------------------- features
def atr_wilder(bars: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = bars["high"], bars["low"], bars["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def features(bars: pd.DataFrame, swing_length: int = 20, lag: int | None = None) -> pd.DataFrame:
    """Causal SMC features (identical to the backtest strat_c.features)."""
    ohlc = bars[["open", "high", "low", "close", "volume"]].copy()
    L = lag if lag is not None else 2 * swing_length
    shl = smc.swing_highs_lows(ohlc, swing_length=swing_length)
    bc = smc.bos_choch(ohlc, shl, close_break=True)
    fv = smc.fvg(ohlc, join_consecutive=False)

    idx = bars.index
    def col(df, name):
        # the library returns a fresh RangeIndex -> re-attach values positionally
        return pd.Series(np.asarray(df[name], dtype=float), index=idx)

    f = pd.DataFrame(index=idx)
    f["bos"] = col(bc, "BOS").shift(L)
    f["choch"] = col(bc, "CHOCH").shift(L)
    f["fvg"] = col(fv, "FVG").shift(1)
    f["fvg_top"] = col(fv, "Top").shift(1)
    f["fvg_bot"] = col(fv, "Bottom").shift(1)
    return f


def signals_limit(bars: pd.DataFrame, feat: pd.DataFrame, swing_length: int = 20,
                  zone: str = "fvg", zone_ttl: int = 150, buffer_atr: float = 0.25,
                  allow_short: bool = True):
    """Reference port of the backtested generator - used for parity tests, not live."""
    close = bars["close"].to_numpy(float)
    atr = atr_wilder(bars, 14).to_numpy(float)
    bos = feat["bos"].to_numpy(float); choch = feat["choch"].to_numpy(float)
    fvv = feat["fvg"].to_numpy(float)
    fvt, fvb = feat["fvg_top"].to_numpy(float), feat["fvg_bot"].to_numpy(float)
    n = len(bars)
    place = np.zeros(n); lmt = np.full(n, np.nan); stp = np.full(n, np.nan)
    bias = 0; z_top = z_bot = np.nan; z_i = -10**9; z_kind = None; z_dir = 0; armed = False
    for i in range(n):
        if bos[i] == 1 or choch[i] == 1: bias = 1
        elif bos[i] == -1 or choch[i] == -1: bias = -1
        new_zone = False
        if fvv[i] == 1 and np.isfinite(fvt[i]):
            z_top, z_bot, z_i, z_kind, z_dir = fvt[i], fvb[i], i, "fvg", 1; new_zone = True
        elif allow_short and fvv[i] == -1 and np.isfinite(fvt[i]):
            z_top, z_bot, z_i, z_kind, z_dir = fvt[i], fvb[i], i, "fvg", -1; new_zone = True
        if new_zone: armed = False
        if z_kind is not None:
            broken = (close[i] < z_bot - 1e-9) if z_dir > 0 else (close[i] > z_top + 1e-9)
            if (i - z_i) > zone_ttl or broken:
                z_top = z_bot = np.nan; z_kind = None; armed = False
        if z_kind is not None and not armed and bias == z_dir and np.isfinite(atr[i]):
            place[i] = z_dir
            lmt[i] = z_top if z_dir > 0 else z_bot
            stp[i] = (z_bot - buffer_atr * atr[i]) if z_dir > 0 else (z_top + buffer_atr * atr[i])
            armed = True
    return (pd.Series(place, index=bars.index), pd.Series(lmt, index=bars.index),
            pd.Series(stp, index=bars.index))
