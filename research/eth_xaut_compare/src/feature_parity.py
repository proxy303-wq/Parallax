"""Are the LIVE features actually identical to the BACKTEST features?

smc_crypto.features() claims in its docstring to be "identical to the backtest
strat_c.features".  This compares them column by column on the same bars.
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

from parallax.core.smc_crypto import features as live_features   # noqa: E402
import strat_c                                                    # noqa: E402

b = pd.read_parquet(BTC / "data" / "delta" / "BTCUSD_1h.parquet")
b = b.set_index("open_time")
b.index = pd.to_datetime(b.index, utc=True)
b = b[["open", "high", "low", "close", "volume"]].astype(float).sort_index().iloc[-1500:]

live = live_features(b, 20)
bt = strat_c.features(b, 20)

print("live columns :", list(live.columns))
print("backtest cols:", [c for c in bt.columns if c in live.columns], "(+ ob/ob_top/ob_bot)")
print()
hdr = "  %-10s %8s %8s %8s %10s" % ("column", "agree%", "live_nz", "bt_nz", "both_nz")
print(hdr)
for c in ("bos", "choch", "fvg", "fvg_top", "fvg_bot"):
    a, z = live[c].to_numpy(float), bt[c].to_numpy(float)
    same_num = np.isclose(a, z, equal_nan=True)
    agree = 100.0 * same_num.mean()
    print("  %-10s %7.1f%% %8d %8d %10d"
          % (c, agree, int((a != 0).sum()), int((z != 0).sum()),
             int(((a != 0) & (z != 0)).sum())))

print()
print("VERDICT:", "IDENTICAL" if all(
    np.isclose(live[c].to_numpy(float), bt[c].to_numpy(float), equal_nan=True).mean() > 0.999
    for c in ("bos", "choch", "fvg", "fvg_top", "fvg_bot")) else "*** DIFFERENT ***")

# where do they first diverge?
for c in ("bos", "choch", "fvg"):
    a, z = live[c].to_numpy(float), bt[c].to_numpy(float)
    d = np.flatnonzero(~np.isclose(a, z, equal_nan=True))
    if len(d):
        i = d[0]
        print("  first %s divergence at %s: live=%s backtest=%s"
              % (c, b.index[i], a[i], z[i]))
