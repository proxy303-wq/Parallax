"""Exhaustive hunt for Binance XAUT history: every plausible pair, archive + REST."""
import io, json, urllib.request, zipfile
from datetime import datetime, timezone

# 1) which XAUT/PAXG pairs does Binance even list?
try:
    r = urllib.request.Request("https://api.binance.com/api/v3/exchangeInfo",
                               headers={"User-Agent": "research/1.0"})
    info = json.loads(urllib.request.urlopen(r, timeout=40).read().decode())
    syms = sorted(s["symbol"] for s in info["symbols"]
                  if "XAUT" in s["symbol"] or "PAXG" in s["symbol"])
    print("Binance spot pairs containing XAUT/PAXG:", syms)
except Exception as e:
    print("exchangeInfo failed:", e)

# 2) earliest kline per candidate, straight from the REST API
for sym in ("XAUTUSDT", "PAXGUSDT", "XAUTUSDC", "PAXGUSDC"):
    try:
        u = ("https://api.binance.com/api/v3/klines?symbol=%s&interval=1h&startTime=%d&limit=1"
             % (sym, int(datetime(2019, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)))
        r = urllib.request.Request(u, headers={"User-Agent": "research/1.0"})
        k = json.loads(urllib.request.urlopen(r, timeout=40).read().decode())
        if k:
            t = datetime.fromtimestamp(k[0][0] / 1000, tz=timezone.utc)
            print("%-10s earliest 1h bar: %s   close=%s" % (sym, t, k[0][4]))
        else:
            print("%-10s no data returned" % sym)
    except Exception as e:
        print("%-10s ERR %s" % (sym, str(e)[:90]))

# 3) Vision archive: every month, monthly + daily fallback presence
COLS = ["open_time","open","high","low","close","volume","ct","qv","trades","tbb","tbq","ig"]
def head(url):
    try:
        r = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status
    except Exception as e:
        return getattr(e, "code", "ERR")

for sym in ("XAUTUSDT", "PAXGUSDT"):
    print("\n== Vision monthly presence: %s ==" % sym)
    have = []
    for y in (2024, 2025, 2026):
        for m in range(1, 13):
            ym = "%04d-%02d" % (y, m)
            st = head(f"https://data.binance.vision/data/spot/monthly/klines/{sym}/1h/{sym}-1h-{ym}.zip")
            if st == 200:
                have.append(ym)
    print("   months available:", have if have else "NONE")
