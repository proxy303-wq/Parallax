"""Three-way gold cross-check: Binance XAUTUSDT vs Delta XAUTUSD vs Binance PAXGUSDT.

XAUT is listed on Binance (2026-03-26) three weeks BEFORE Delta India (2026-04-17), so the
Binance series both extends the window and independently corroborates the Delta prices.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run_wfo import load   # noqa: E402

D = HERE.parent / "data"
series = {
    "XAUT-binance": load(str(D / "XAUTUSDT_binance_1h.parquet")).close,
    "XAUT-delta": load(str(D / "XAUTUSD_india_1h.parquet")).close,
    "PAXG-binance": load(str(D / "PAXGUSDT_binance_1h.parquet")).close,
}

print("series coverage")
for k, s in series.items():
    print("  %-14s %6d bars  %s -> %s   range $%.2f - $%.2f"
          % (k, len(s), s.index[0], s.index[-1], s.min(), s.max()))

j = pd.concat(series, axis=1).dropna()
print("\nthree-way overlap: %d hourly bars  %s -> %s" % (len(j), j.index[0], j.index[-1]))
print("\npairwise correlation")
for a in series:
    for b in series:
        if a < b:
            print("  %-14s vs %-14s  r = %.8f   mean basis %+0.4f%%   mean |diff| $%.2f"
                  % (a, b, j[a].corr(j[b]),
                     100 * ((j[a] / j[b] - 1).mean()),
                     (j[a] - j[b]).abs().mean()))

# does the Binance XAUT series agree with Delta over the whole Delta window?
sub = j[["XAUT-binance", "XAUT-delta"]].dropna()
print("\nXAUT Binance vs Delta on their overlap (%d bars): r=%.8f  max |diff| $%.2f"
      % (len(sub), sub["XAUT-binance"].corr(sub["XAUT-delta"]),
         (sub["XAUT-binance"] - sub["XAUT-delta"]).abs().max()))
