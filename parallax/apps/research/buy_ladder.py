"""ATM-relative option ladders for expiries FURTHER OUT than the front week.

Why this exists: the buying system was first tested on the front-week contract,
which on most days is 0-6 days from expiry.  That is the steepest part of the
theta curve and the worst possible place for a buyer - Katwal puts the
acceleration at about 13 days, so a buyer wants to be well outside that window.
For NIFTY weeklies that means expiryCode 3 or 4, roughly 15 to 30 days out.
(The plan's own example - buy the 23,150 CE at 180 with spot at 23,140 - prices
out at expiryCode 3 almost exactly, which is what gave the game away.)

THE ROLLING TRAP.  The endpoint is ATM-RELATIVE and it rolls by itself: the
series labelled "expiryCode 3" always means whatever is third-nearest TODAY, so
a contract ages from code 3 to code 2 to code 1 and then dies.  A single code's
series therefore does NOT follow one contract, and holding a position across a
weekly rollover while reading one code would silently swap the instrument
underneath the position - the same class of bug as reading a fixed strike at the
current ATM.  Following one expiry through its life means stitching the codes
together day by day; that lives in buy_panel.py.

Usage:
    python -m parallax.apps.research.buy_ladder --index NIFTY --code 3
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import time as _t
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_OFF = 10          # ladder depth in strikes each way
WINDOW_DAYS = 27


def arg(flag, default):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def windows(start: datetime.date, end: datetime.date, days: int = WINDOW_DAYS):
    out, d = [], start
    while d < end:
        e = min(d + datetime.timedelta(days=days), end)
        out.append((d.isoformat(), e.isoformat()))
        d = e
    return out


def fetch(tok, idx, off, otype, code, f, t, client_id=""):
    """One rolling-option series.  Retries hard: a 401 here is the rate limiter."""
    b = {"exchangeSegment": idx.exchange_segment, "interval": "5",
         "securityId": idx.underlying_id, "instrument": "OPTIDX",
         "expiryFlag": "WEEK" if idx.symbol in ("NIFTY", "SENSEX") else "MONTH",
         "expiryCode": int(code), "strike": off, "drvOptionType": otype,
         "requiredData": ["close", "high", "low", "spot"],
         "fromDate": f, "toDate": t}
    for a in range(4):
        try:
            req = urllib.request.Request(
                "https://api.dhan.co/v2/charts/rollingoption",
                data=json.dumps(b).encode(),
                headers={"Accept": "application/json",
                         "Content-Type": "application/json",
                         "access-token": tok, "client-id": client_id},
                method="POST")
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.loads(r.read().decode())
            return (d.get("data") or {}).get("ce" if otype == "CALL" else "pe") or {}
        except Exception:
            if a == 3:
                return {}
            _t.sleep(4.0 * (a + 1))
    return {}


def load(symbol, code, start="2025-01-05", end="2026-09-01", workers=6,
         progress=print):
    """{ts: {spot, CALL:{offset:(c,h,l)}, PUT:{...}}} at the given expiryCode.

    FETCHED CONCURRENTLY, because one call takes ~16s against a ~30 day window
    and serialising the whole thing is a nine-hour job.  Dhan caps the window at
    about 30 days (60 and 90 both come back empty), so there is no way to buy the
    time back with fewer, larger requests - only with parallelism.  Six workers
    measured ~3.7s per call effective, with no rate-limit failures.

    The cache is rewritten every 100 calls as well as at the end: a nine-hour
    job that only saves on completion loses everything to one bad call.
    """
    import pickle
    from concurrent.futures import ThreadPoolExecutor
    from parallax.adapters.broker.dhan_auth import active_token
    from parallax.config.indices import spec

    cache = os.path.join(HERE, "_ladder_%s_C%s_%s_%s.pkl" % (symbol, code, start, end))
    try:
        with open(cache, "rb") as fh:
            d = pickle.load(fh)
        progress("  loaded %d bars from %s" % (len(d), os.path.basename(cache)))
        return d
    except (OSError, ValueError, pickle.UnpicklingError):
        pass

    tok, _ = active_token()
    client_id = os.environ.get("DHAN_CLIENT_ID", "")
    idx = spec(symbol)
    step = float(idx.step)
    offs = ["ATM"] + ["ATM%+d" % j for j in range(-MAX_OFF, MAX_OFF + 1) if j != 0]
    wins = windows(datetime.date.fromisoformat(start), datetime.date.fromisoformat(end))

    tasks = []
    for off in offs:
        n = 0 if off == "ATM" else int(off.replace("ATM", "").replace("+", ""))
        for ot in ("CALL", "PUT"):
            for f, t in wins:
                tasks.append((off, ot, n, f, t))

    byts: dict = {}
    done = {"n": 0}

    def work(task):
        off, ot, n, f, t = task
        return n, ot, fetch(tok, idx, off, ot, code, f, t, client_id)

    def absorb(n, ot, d):
        ts = d.get("timestamp") or []
        sp = d.get("spot") or []
        cl = d.get("close") or []
        hi = d.get("high") or []
        lo = d.get("low") or []
        for i, tv in enumerate(ts):
            if i >= len(cl) or i >= len(sp):
                break
            row = byts.setdefault(int(tv), {"spot": float(sp[i]),
                                            "CALL": {}, "PUT": {}})
            row[ot][n * step] = (float(cl[i]),
                                 float(hi[i]) if i < len(hi) else float(cl[i]),
                                 float(lo[i]) if i < len(lo) else float(cl[i]))

    def save():
        try:
            with open(cache, "wb") as fh:
                pickle.dump(byts, fh)
        except OSError:
            pass

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for n, ot, d in ex.map(work, tasks):
            absorb(n, ot, d)
            done["n"] += 1
            if done["n"] % 100 == 0:
                progress("  C%s %s %d/%d calls, %d bars"
                         % (code, symbol, done["n"], len(tasks), len(byts)))
                save()
    save()
    return byts


def main() -> None:
    symbol = str(arg("--index", "NIFTY")).upper()
    codes = [c.strip() for c in str(arg("--code", "3")).split(",")]
    start = str(arg("--start", "2025-01-05"))
    end = str(arg("--end", "2026-09-01"))
    workers = int(arg("--workers", "6"))
    for c in codes:
        print("fetching %s expiryCode %s (%d workers)" % (symbol, c, workers),
              flush=True)
        d = load(symbol, c, start, end, workers=workers,
                 progress=lambda m: print(m, flush=True))
        print("  -> %d bars" % len(d), flush=True)


if __name__ == "__main__":
    main()
