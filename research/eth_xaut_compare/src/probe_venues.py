"""Probe which venue carries which symbol, and from when. A wide request is not an
error -- the endpoint silently returns only the most recent ~4000 bars, so coverage must
be probed in BOUNDED windows (one week each) and never inferred from a wide query."""
import json, time, urllib.request
from datetime import datetime, timezone

URLS = {"india": "https://api.india.delta.exchange/v2/history/candles",
        "global": "https://api.delta.exchange/v2/history/candles"}


def month_probe(symbol, venue, year, month):
    url = URLS[venue]
    start = int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp())
    end = start + 27 * 86400
    q = "resolution=1h&symbol=%s&start=%d&end=%d" % (symbol, start, end)
    try:
        r = urllib.request.Request(url + "?" + q, headers={"Accept": "application/json",
                                                           "User-Agent": "research/1.0"})
        d = json.loads(urllib.request.urlopen(r, timeout=40).read().decode())
        rows = d.get("result", []) or []
        if not rows:
            return 0, None, None, None
        import statistics
        vols = [float(x.get("volume", 0) or 0) for x in rows]
        zv = 100.0 * sum(1 for v in vols if v <= 0) / len(vols)
        t0 = datetime.fromtimestamp(int(rows[0]["time"]), tz=timezone.utc)
        t1 = datetime.fromtimestamp(int(rows[-1]["time"]), tz=timezone.utc)
        return len(rows), t0.date(), t1.date(), zv
    except Exception as e:
        return -1, type(e).__name__, str(e)[:60], None


for symbol in ("ETHUSD", "XAUTUSD"):
    for venue in ("india", "global"):
        print("== %s / %s ==" % (symbol, venue))
        for year, month in [(2024,1),(2024,3),(2024,6),(2024,9),(2025,1),(2025,6),(2026,1),(2026,4),(2026,7)]:
            n, a, b, zv = month_probe(symbol, venue, year, month)
            if n > 0:
                print("   %d-%02d: %4d bars  %s -> %s  zerovol=%0.1f%%" % (year, month, n, a, b, zv))
            else:
                print("   %d-%02d: EMPTY/%s %s" % (year, month, a, b))
        time.sleep(0.3)
