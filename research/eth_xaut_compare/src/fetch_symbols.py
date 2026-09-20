"""Fetch Delta Exchange (India) 1h candles for ETHUSD / XAUTUSD, 2024-01-01 -> now.

Backward pagination in bounded windows, exactly like the finalized BTC fetcher, because
the public /v2/history/candles endpoint caps a response at ~4000 bars and silently returns
the MOST RECENT slice of a wide window (a wide request is not an error -- it just lies).
"""
import json, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

OUT = Path(r"C:\PrOxyTradingTerminal\.research\eth_xaut_compare\data")
OUT.mkdir(parents=True, exist_ok=True)
VENUES = {"india": "https://api.india.delta.exchange",
          "global": "https://api.global.delta.exchange"}
SEC = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}


def fetch(symbol, res, start, end, venue="india", window_bars=1000, sleep=0.25):
    url = VENUES[venue] + "/v2/history/candles"
    step = SEC[res] * window_bars
    out, cursor, req, fails_total = [], end, 0, 0
    while cursor > start:
        seg_start = max(start, cursor - step)
        q = f"resolution={res}&symbol={symbol}&start={seg_start}&end={cursor}"
        got = False
        for attempt in range(4):
            try:
                r = urllib.request.Request(f"{url}?{q}",
                                           headers={"Accept": "application/json",
                                                    "User-Agent": "research/1.0"})
                with urllib.request.urlopen(r, timeout=40) as resp:
                    d = json.loads(resp.read().decode())
                out.extend(d.get("result", []) or [])
                got = True
                break
            except Exception as e:
                if attempt == 3:
                    print(f"    !! give up {seg_start}-{cursor}: {e}", flush=True)
                    fails_total += 1
                time.sleep(1.5 * (attempt + 1))
        req += 1
        if req % 25 == 0:
            print(f"    {symbol} {res} {venue}: {req} req, {len(out)} raw, "
                  f"cursor={datetime.fromtimestamp(cursor, tz=timezone.utc)}", flush=True)
        cursor = seg_start
        time.sleep(sleep)
    return out, req, fails_total


def clean(raw, res):
    if not raw:
        return pd.DataFrame()
    df = pd.DataFrame(raw)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["open_time"] = pd.to_datetime(df["time"].astype("int64"), unit="s", utc=True)
    df = (df[["open_time", "open", "high", "low", "close", "volume"]].dropna()
            .drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True))
    return df


if __name__ == "__main__":
    START = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    END = int(time.time())
    for symbol in ("ETHUSD", "XAUTUSD"):
        for venue in ("india", "global"):
            t0 = time.time()
            raw, req, fails = fetch(symbol, "1h", START, END, venue=venue)
            df = clean(raw, "1h")
            if df.empty:
                print(f"{symbol} {venue} 1h: NO DATA ({req} req)", flush=True)
                continue
            path = OUT / f"{symbol}_{venue}_1h.parquet"
            df.to_parquet(path, index=False)
            step = 3600
            gap = (df.open_time.diff().dt.total_seconds().div(step) - 1)
            bad = df[(df.high < df.low) | (df.high < df.open) | (df.high < df.close) |
                     (df.low > df.open) | (df.low > df.close) | (df.close <= 0)]
            print(f"{symbol} {venue} 1h: {len(df):>7d} bars {df.open_time.iloc[0]} -> "
                  f"{df.open_time.iloc[-1]} req={req} fails={fails} {time.time()-t0:.0f}s "
                  f"malformed={len(bad)} missing={int(gap.clip(lower=0).sum())} "
                  f"zerovol={100*(df.volume <= 0).mean():.1f}%", flush=True)
    print("DONE", flush=True)
