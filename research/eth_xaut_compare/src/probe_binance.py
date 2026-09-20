"""Which gold series has real 1h history on Binance Vision covering 2024-01 -> 2026-09?"""
import urllib.request

BASE = "https://data.binance.vision/data/spot/monthly/klines/%s/1h/%s-1h-%s.zip"
for sym in ("XAUTUSDT", "PAXGUSDT", "PAXGUSDC", "BTCUSDT"):
    print("== %s ==" % sym)
    for ym in ("2024-01", "2024-06", "2025-01", "2025-06", "2026-01", "2026-06", "2026-08", "2026-09"):
        url = BASE % (sym, sym, ym)
        try:
            r = urllib.request.Request(url, method="HEAD",
                                       headers={"User-Agent": "research/1.0"})
            with urllib.request.urlopen(r, timeout=30) as resp:
                print("   %s  %s  %s bytes" % (ym, resp.status, resp.headers.get("Content-Length")))
        except Exception as e:
            print("   %s  MISSING (%s)" % (ym, getattr(e, "code", type(e).__name__)))
