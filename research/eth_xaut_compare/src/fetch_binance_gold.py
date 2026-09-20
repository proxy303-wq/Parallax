"""Binance Vision 1h klines for gold proxies, 2024-01 -> 2026-09.

Two data hazards this handles, both of which silently corrupt a backtest:
  1. Binance Vision switched open_time UNITS mid-archive (ms before ~2025, us after).
     Parsing everything as microseconds moves 2024 to 1970-01-19 -- a plausible-looking
     but three-years-deleted series.  Detect the unit per file from its magnitude.
  2. The current month is only available as DAILY files, so monthly + daily are merged.
"""
import io, time, urllib.request, zipfile
from pathlib import Path

import pandas as pd

OUT = Path(r"C:\PrOxyTradingTerminal\.research\eth_xaut_compare\data")
OUT.mkdir(parents=True, exist_ok=True)
COLS = ["open_time", "open", "high", "low", "close", "volume", "ct", "qv",
        "trades", "tbb", "tbq", "ig"]


def months(a, b):
    y, m = int(a[:4]), int(a[5:]); ry, rm = int(b[:4]), int(b[5:]); out = []
    while (y, m) <= (ry, rm):
        out.append("%04d-%02d" % (y, m)); m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def grab(url):
    for _ in range(3):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(r, timeout=60).read()
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                df = pd.read_csv(z.open(z.namelist()[0]), header=None, names=COLS)
            if str(df.iloc[0, 0]).startswith("open_time"):
                df = df.iloc[1:]
            return df
        except Exception:
            time.sleep(0.4)
    return None


def normalise(frame):
    """Parse ONE file's open_time, choosing ms vs us from that file's own magnitude.

    This must be per file: the archive switched units mid-history, so a single global
    choice silently moves half the sample to the year 56971 (or to 1970).
    """
    v = pd.to_numeric(frame["open_time"], errors="coerce")
    tick = v.dropna().iloc[0] if len(v.dropna()) else 0
    unit = "us" if tick > 1e14 else "ms"
    frame = frame.copy()
    frame["open_time"] = pd.to_datetime(v.astype("int64"), unit=unit, utc=True)
    return frame, unit


def fetch(symbol, tf="1h"):
    frames, miss, units = [], [], set()
    for mo in months("2024-01", "2026-08"):
        d = grab(f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/{tf}/{symbol}-{tf}-{mo}.zip")
        if d is None:
            # fall back to daily files for months with no monthly archive
            got = False
            for day in range(1, 29):
                dd = f"{mo}-{day:02d}"
                x = grab(f"https://data.binance.vision/data/spot/daily/klines/{symbol}/{tf}/{symbol}-{tf}-{dd}.zip")
                if x is not None:
                    frames.append(x); got = True
            if not got:
                miss.append(mo)
        else:
            frames.append(d)
    for day in ["2026-09-%02d" % d for d in range(1, 20)]:
        x = grab(f"https://data.binance.vision/data/spot/daily/klines/{symbol}/{tf}/{symbol}-{tf}-{day}.zip")
        if x is not None:
            frames.append(x)
        else:
            miss.append(day)
    if not frames:
        return pd.DataFrame(), miss, units
    normed = []
    for fr in frames:
        fr2, unit = normalise(fr)
        units.add(unit)
        normed.append(fr2)
    df = pd.concat(normed, ignore_index=True)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = (df[["open_time", "open", "high", "low", "close", "volume"]].dropna()
            .drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True))
    df = df[df.open_time < pd.Timestamp("2026-09-20", tz="UTC")]
    return df, miss, units


if __name__ == "__main__":
    for symbol in ("PAXGUSDT", "XAUTUSDT"):
        t0 = time.time()
        df, miss, units = fetch(symbol)
        if df.empty:
            print(symbol, "NO DATA"); continue
        df.to_parquet(OUT / f"{symbol}_binance_1h.parquet", index=False)
        gap = (df.open_time.diff().dt.total_seconds().div(3600) - 1).clip(lower=0).sum()
        print("%s: %d bars  %s -> %s  units=%s  missing_files=%d  missing_slots=%d  "
              "zerovol=%d  price=[%.2f, %.2f]  %.0fs"
              % (symbol, len(df), df.open_time.iloc[0], df.open_time.iloc[-1], sorted(units),
                 len(miss), int(gap), int((df.volume <= 0).sum()),
                 df.low.min(), df.high.max(), time.time() - t0), flush=True)
