"""Binance Vision 1h for XAUTUSDT -- the pair only listed 2026-03-26, so the window is short.

This gives a THIRD independent gold series (Binance XAUT) to cross-check Delta's XAUTUSD
and Binance's PAXGUSDT.  Unit detection (ms vs us) is per file.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fetch_binance_gold as G   # reuse grab/months/normalise

SYMBOL = "XAUTUSDT"
START_MONTH = "2026-03"


def main():
    frames, miss, units = [], [], set()
    for mo in G.months(START_MONTH, "2026-08"):
        d = G.grab("https://data.binance.vision/data/spot/monthly/klines/%s/1h/%s-1h-%s.zip"
                   % (SYMBOL, SYMBOL, mo))
        if d is None:
            miss.append(mo)
        else:
            frames.append(d)
    for day in ["2026-09-%02d" % d for d in range(1, 20)]:
        x = G.grab("https://data.binance.vision/data/spot/daily/klines/%s/1h/%s-1h-%s.zip"
                   % (SYMBOL, SYMBOL, day))
        if x is not None:
            frames.append(x)
        else:
            miss.append(day)

    normed = []
    for fr in frames:
        fr2, unit = G.normalise(fr)
        units.add(unit)
        normed.append(fr2)
    df = pd.concat(normed, ignore_index=True)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = (df[["open_time", "open", "high", "low", "close", "volume"]].dropna()
            .drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True))
    df = df[df.open_time < pd.Timestamp("2026-09-20", tz="UTC")]
    out = G.OUT / (SYMBOL + "_binance_1h.parquet")
    df.to_parquet(out, index=False)
    gap = int((df.open_time.diff().dt.total_seconds().div(3600) - 1).clip(lower=0).sum())
    print("saved:", out)
    print("%s: %d bars  %s -> %s  units=%s  missing_files=%d  missing_slots=%d  "
          "zerovol=%d  price=[%.2f, %.2f]"
          % (SYMBOL, len(df), df.open_time.iloc[0], df.open_time.iloc[-1], sorted(units),
             len(miss), gap, int((df.volume <= 0).sum()), df.low.min(), df.high.max()))
    if miss:
        print("   missing:", miss)


if __name__ == "__main__":
    main()
