"""Root-cause the live/backtest divergence and test the fix.

Hypothesis: smc_crypto.features() claims to be "identical to the backtest
strat_c.features" but calls

    smc.swing_highs_lows(ohlc, swing_length=swing_length)      # 20

where the backtest calls

    smc.swing_highs_lows(ohlc, swing_length=L)                 # L = 2*swing_length = 40

so the live twin detects structure on half the swing window.  A second, independent
divergence is CONFIRM=60, which delays every decision by 60 bars even though the
measured causal minimum is far smaller.

This patches the live features to mirror the backtest exactly, then re-measures the
entry match that the repo's own equiv_inc.py reports as 15/41.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(r"C:\Parallax")
BTC = Path(r"C:\PrOxyTradingTerminal\.research\btc_jas_compare")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(BTC / "src"))
sys.path.insert(0, str(BTC / "repos" / "smart-money-concepts"))

import parallax.core.smc_crypto as SC                      # noqa: E402
from parallax.config.crypto import SMCConfig               # noqa: E402
from engine import run, Costs, Spec, atr_wilder            # noqa: E402
import strat_c                                             # noqa: E402
from smartmoneyconcepts import smc                         # noqa: E402

GST = 1.18
FULL = pd.read_parquet(BTC / "data" / "spliced" / "BTCUSD_1h.parquet").set_index("open_time")
FULL.index = pd.to_datetime(FULL.index, utc=True)
world = FULL.iloc[-2000:][["open", "high", "low", "close", "volume"]].astype(float)
CAP = 8_00_000 / 88.0
C = Costs(fee=0, slip=0.0001, fee_open=0.0002 * GST, fee_close=0.0005 * GST,
          fee_close_limit=0.0002 * GST, funding_per_bar=0.0001 / 8)


def fixed_features(bars, swing_length=20, lag=None):
    """Byte-equivalent to strat_c.features: swing window is L, not swing_length."""
    ohlc = bars[["open", "high", "low", "close", "volume"]].copy()
    L = lag if lag is not None else 2 * swing_length
    shl = smc.swing_highs_lows(ohlc, swing_length=L)
    bc = smc.bos_choch(ohlc, shl, close_break=True)
    fv = smc.fvg(ohlc, join_consecutive=False)
    idx = bars.index

    def col(df, name):
        return pd.Series(np.asarray(df[name], dtype=float), index=idx)

    f = pd.DataFrame(index=idx)
    f["bos"] = col(bc, "BOS").shift(L)
    f["choch"] = col(bc, "CHOCH").shift(L)
    f["fvg"] = col(fv, "FVG").shift(1)
    f["fvg_top"] = col(fv, "Top").shift(1)
    f["fvg_bot"] = col(fv, "Bottom").shift(1)
    return f


def engine_trades():
    feat = strat_c.features(world, 20)
    pa, la, sa = strat_c.signals_limit(world, feat, zone="fvg", allow_short=True)
    spec = Spec(entries=pd.Series(np.asarray(pa, float), index=world.index),
                exit_signal=pd.Series(False, index=world.index), atr=atr_wilder(world, 14),
                stop_price=pd.Series(np.asarray(sa, float), index=world.index),
                limit_entries=True, limit_price=pd.Series(np.asarray(la, float), index=world.index),
                limit_ttl=20, trail_mult=5.0, target_R=None, risk_pct=0.0075,
                allow_short=True, stop_atr_floor=1.5, max_leverage=5.0,
                max_notional=100000.0, maintenance_rate=0.0025)
    res = run(world, spec, CAP, C)
    return res["trades"]


def live_fills(confirm):
    m = SC.SMCCrypto(SMCConfig(), confirm=confirm)
    fills = []
    for k in range(len(world)):
        for dec in m.step(world.iloc[:k + 1]):
            if dec.action == "fill":
                qty = m.size(CAP, dec.price, dec.stop)
                if qty > 0:
                    m.open_position(dec.side, dec.price, qty, dec.stop,
                                    abs(dec.price - dec.stop) * qty, ts=dec.ts)
                    fills.append(dec.ts)
                else:
                    m.position = None
    return fills, m.bar_count


def compare(label, fills, et):
    se, sl = set(et.entry_time), set(fills)
    print("   %-34s engine %d | live %d | EXACT %d | engine-only %d | live-only %d"
          % (label, len(se), len(sl), len(se & sl), len(se - sl), len(sl - se)))
    return len(se & sl)


if __name__ == "__main__":
    et = engine_trades()
    print("ENGINE trades:", len(et))
    print()
    print("== as deployed (live features, CONFIRM=60) ==")
    f, bc = live_fills(60)
    compare("current code", f, et)
    print("      bars processed %d of %d (lag %d)" % (bc, len(world), len(world) - 1 - bc))

    print()
    print("== with the features patched to match the backtest ==")
    SC.features = fixed_features
    for confirm in (60, 41, 21, 11):
        f, bc = live_fills(confirm)
        n = compare("fixed features, CONFIRM=%d" % confirm, f, et)
        print("      bars processed %d of %d (lag %d)" % (bc, len(world), len(world) - 1 - bc))
