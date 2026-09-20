"""Does the Binance PAXG series actually track Delta's XAUTUSD?  A proxy is only
admissible if the two agree -- otherwise the gold result is about a different asset."""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run_wfo import load   # noqa: E402

D = HERE.parent / "data"
paxg = load(str(D / "PAXGUSDT_binance_1h.parquet"))
xaut = load(str(D / "XAUTUSD_india_1h.parquet"))

j = pd.concat([paxg.close.rename("paxg"), xaut.close.rename("xaut")], axis=1).dropna()
print("overlapping 1h bars:", len(j))
print("window: %s -> %s" % (j.index[0], j.index[-1]))
if len(j) > 10:
    print("correlation       : %.8f" % j.paxg.corr(j.xaut))
    basis = (j.paxg / j.xaut - 1) * 100
    print("mean basis (PAXG/XAUT-1): %+.4f%%" % basis.mean())
    print("median basis      : %+.4f%%" % basis.median())
    print("mean |diff| USD   : $%.2f" % (j.paxg - j.xaut).abs().mean())
    print("PAXG range        : $%.2f - $%.2f" % (paxg.low.min(), paxg.high.max()))
    print("XAUT range        : $%.2f - $%.2f" % (xaut.low.min(), xaut.high.max()))
    print("\nmonthly mean basis:")
    print(basis.groupby(basis.index.strftime("%Y-%m")).mean().round(4).to_string())
