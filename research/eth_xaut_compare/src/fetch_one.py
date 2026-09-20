"""Fetch one symbol/venue/res from Delta India and report data quality."""
import sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fetch_symbols as F

symbol = sys.argv[1] if len(sys.argv) > 1 else "XAUTUSD"
venue = sys.argv[2] if len(sys.argv) > 2 else "india"
res = sys.argv[3] if len(sys.argv) > 3 else "1h"

START = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
END = int(time.time())
t0 = time.time()
raw, req, fails = F.fetch(symbol, res, START, END, venue=venue)
df = F.clean(raw, res)
if df.empty:
    print(symbol, venue, res, "NO DATA ->", req, "requests", fails, "failures")
    raise SystemExit(1)
path = F.OUT / (symbol + "_" + venue + "_" + res + ".parquet")
df.to_parquet(path, index=False)
step = F.SEC[res]
gap = (df.open_time.diff().dt.total_seconds().div(step) - 1)
bad = df[(df.high < df.low) | (df.high < df.open) | (df.high < df.close) |
         (df.low > df.open) | (df.low > df.close) | (df.close <= 0)]
print("saved:", path)
print("%s %s %s: %d bars  %s -> %s  req=%d fails=%d  %.0fs" %
      (symbol, venue, res, len(df), df.open_time.iloc[0], df.open_time.iloc[-1],
       req, fails, time.time() - t0))
print("malformed=%d  missing_slots=%d  zerovol=%0.1f%%  price_range=[%.2f, %.2f]" %
      (len(bad), int(gap.clip(lower=0).sum()), 100 * (df.volume <= 0).mean(),
       df.low.min(), df.high.max()))
