"""Does the buying plan's SIGNAL have an edge?  Tested on the underlying alone.

The plan (NIFTY hedged high-probability option buying) has two layers: a
15-minute trend filter plus a 5-minute structure breakout, expressed with a
debit spread.  The spread layer is arithmetic - it caps the payoff and charges a
debit for the privilege - so it cannot manufacture an edge the signal does not
already have.  It can only dilute one.

So this module tests the SIGNAL, on the index, before any option work is built.
If a confirmed breakout inside a confirmed trend does not move NIFTY, no strike
selection and no exit rule will rescue it, and the honest answer is to stop.

Causality is the whole game here.  Every series below is read at the 5-minute
bar it would have been available at:

  * a 15-minute bar stamped T is only CLOSED at T+15, so at 5m bar i the newest
    usable 15m bar is (bars elapsed this session // 3) - 1
  * a swing high needs its right-hand bars to have printed before it can be
    confirmed, so it becomes usable only at p+R
  * entry fills at the OPEN of the bar after the signal

Usage:
    python -m parallax.apps.research.buy_signal --index NIFTY
"""
from __future__ import annotations

import datetime
import os
import pickle
import sys
import time as _t

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
HERE = os.path.dirname(os.path.abspath(__file__))

#: 5-min bars in a NIFTY session: 09:15 .. 15:25 inclusive
BARS_PER_SESSION = 75
#: swing confirmation bars either side
SWING_L, SWING_R = 2, 2
#: volume confirmation window
VOL_N = 20
#: a trade must be able to finish before the bell
LAST_ENTRY_LOCAL = 60          # 09:15 + 60*5m = 14:15
FIRST_ENTRY_LOCAL = 6          # 09:45


def arg(flag: str, default):
    """--flag value from argv, else default."""
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


# ---------------------------------------------------------------- data ----
def _scrip(symbol):
    from parallax.config.indices import spec
    return str(spec(symbol).underlying_id)


def fetch_bars(symbol="NIFTY", start="2025-01-05", end="2026-09-01", progress=print):
    """5-min OHLCV for the underlying, chunked to Dhan's 90-day intraday limit."""
    cache = os.path.join(HERE, "_bars_%s_%s_%s.pkl" % (symbol, start, end))
    try:
        with open(cache, "rb") as fh:
            d = pickle.load(fh)
        progress("  loaded %d bars from cache" % len(d["ts"]))
        return d
    except (OSError, ValueError, pickle.UnpicklingError):
        pass

    from parallax.adapters.broker.dhan import DhanBroker
    api = DhanBroker(dry_run=True)._api_client()
    scrip = _scrip(symbol)
    seen = {}
    d0 = datetime.date.fromisoformat(start)
    d1 = datetime.date.fromisoformat(end)
    cur = d0
    while cur < d1:
        nxt = min(cur + datetime.timedelta(days=80), d1)
        frm = cur.isoformat() + " 09:15:00"
        to = nxt.isoformat() + " 15:30:00"
        try:
            res = api.intraday_minute_data(scrip, "IDX_I", "INDEX", frm, to, "5")
        except Exception as e:
            progress("  %s..%s failed: %s" % (cur, nxt, type(e).__name__))
            res = None
        d = (res or {}).get("data") or {}
        ts = d.get("timestamp") or []
        op = d.get("open") or []
        hi = d.get("high") or []
        lo = d.get("low") or []
        cl = d.get("close") or []
        vo = d.get("volume") or []
        got = 0
        for i in range(len(ts)):
            if i >= len(cl):
                break
            seen[int(ts[i])] = (float(op[i]) if i < len(op) else float(cl[i]),
                                float(hi[i]) if i < len(hi) else float(cl[i]),
                                float(lo[i]) if i < len(lo) else float(cl[i]),
                                float(cl[i]),
                                float(vo[i]) if i < len(vo) else 0.0)
            got += 1
        progress("  %s..%s  %d bars (total %d)" % (cur, nxt, got, len(seen)))
        cur = nxt
        _t.sleep(0.3)

    keys = sorted(seen)
    out = {"ts": keys,
           "o": [seen[k][0] for k in keys],
           "h": [seen[k][1] for k in keys],
           "l": [seen[k][2] for k in keys],
           "c": [seen[k][3] for k in keys],
           "v": [seen[k][4] for k in keys]}
    try:
        with open(cache, "wb") as fh:
            pickle.dump(out, fh)
    except OSError:
        pass
    return out


def session_spans(bars):
    """[(date, i0, i1)] - contiguous in-session runs, i1 exclusive."""
    out, cur = [], None
    for i, ts in enumerate(bars["ts"]):
        day = datetime.datetime.fromtimestamp(ts, IST).date()
        if cur is None or day != cur[0]:
            if cur is not None:
                out.append((cur[0], cur[1], i))
            cur = (day, i)
    if cur is not None:
        out.append((cur[0], cur[1], len(bars["ts"])))
    return out


# ----------------------------------------------------------- indicators ----
def ema(vals, n):
    """EMA seeded with the simple average; None until n samples exist."""
    out = [None] * len(vals)
    if len(vals) < n:
        return out
    k = 2.0 / (n + 1.0)
    prev = sum(vals[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(vals)):
        prev = vals[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def build_15m(bars, spans):
    """15-minute bars aligned to the 09:15 open, plus the closed-count map."""
    m15 = []
    closed = [0] * len(bars["ts"])       # one past the newest CLOSED 15m bar
    for day, i0, i1 in spans:
        base = len(m15)
        n = i1 - i0
        for j in range(n // 3):
            a = i0 + 3 * j
            b = a + 3
            m15.append((day, max(bars["h"][a:b]), min(bars["l"][a:b]),
                        bars["c"][b - 1]))
        for k in range(i0, i1):
            local = k - i0
            # the 15m bar covering [3q, 3q+2] is closed only after 5m bar 3q+2,
            # so at local bar k exactly k//3 of them have closed
            closed[k] = base + (local // 3)
    return m15, closed


def vwap_series(bars, spans):
    """Running session VWAP on typical price x volume, available at each bar."""
    out = [None] * len(bars["ts"])
    for day, i0, i1 in spans:
        pv = 0.0
        vv = 0.0
        for i in range(i0, i1):
            tp = (bars["h"][i] + bars["l"][i] + bars["c"][i]) / 3.0
            v = bars["v"][i] or 1.0
            pv += tp * v
            vv += v
            out[i] = pv / vv if vv else None
    return out


def swings(bars, spans, left=SWING_L, right=SWING_R):
    """[(confirm_index, kind, price)] - a swing exists only once confirmed."""
    out = []
    for day, i0, i1 in spans:
        for p in range(i0 + left, i1 - right):
            h, l = bars["h"][p], bars["l"][p]
            if all(h > bars["h"][p - k] for k in range(1, left + 1)) and \
               all(h > bars["h"][p + k] for k in range(1, right + 1)):
                out.append((p + right, "H", h))
            if all(l < bars["l"][p - k] for k in range(1, left + 1)) and \
               all(l < bars["l"][p + k] for k in range(1, right + 1)):
                out.append((p + right, "L", l))
    out.sort()
    return out


def last_swing_before(sw, i, kind):
    """The newest swing of that kind confirmed at or before bar i."""
    best = None
    for at, k, px in sw:
        if at > i:
            break
        if k == kind:
            best = px
    return best


# -------------------------------------------------------------- signal ----
def signals(bars, spans):
    """One row per eligible 5m bar: regime, trigger, and the entry that follows."""
    m15, closed = build_15m(bars, spans)
    c15 = [x[3] for x in m15]
    e20 = ema(c15, 20)
    e50 = ema(c15, 50)
    vw = vwap_series(bars, spans)
    sw = swings(bars, spans)

    rows = []
    for day, i0, i1 in spans:
        for i in range(i0, i1):
            local = i - i0
            if local < FIRST_ENTRY_LOCAL or local > LAST_ENTRY_LOCAL:
                continue
            if i + 2 >= len(bars["ts"]):
                continue
            j = closed[i] - 1                     # newest CLOSED 15m bar
            if j < 51 or e20[j] is None or e50[j] is None or e20[j - 1] is None:
                continue
            if vw[i] is None:
                continue
            c = bars["c"][i]
            slope = e20[j] - e20[j - 1]
            if c > vw[i] and e20[j] > e50[j] and slope > 0:
                regime = "BULL"
            elif c < vw[i] and e20[j] < e50[j] and slope < 0:
                regime = "BEAR"
            else:
                regime = "NEUTRAL"

            sh = last_swing_before(sw, i, "H")
            sl = last_swing_before(sw, i, "L")
            vol_ok = False
            med = None
            if i - VOL_N >= i0:
                win = bars["v"][i - VOL_N:i]
                avg = sum(win) / len(win) if win else 0.0
                vol_ok = avg > 0 and bars["v"][i] > avg
                sizes = sorted(bars["h"][k] - bars["l"][k]
                               for k in range(i - VOL_N, i))
                med = sizes[len(sizes) // 2]
            rng = bars["h"][i] - bars["l"][i]
            exp_ok = med is None or med <= 0 or rng < 2.0 * med

            rows.append({"i": i, "day": day, "regime": regime,
                         "broke_up": sh is not None and c > sh,
                         "broke_dn": sl is not None and c < sl,
                         "vol_ok": vol_ok, "exp_ok": exp_ok,
                         "entry": bars["o"][i + 1]})
    return rows


# ------------------------------------------------------------ forward -----
def forward(bars, spans, row, horizon):
    """Move from the entry fill to that many bars later, or the session bell."""
    i = row["i"]
    i1 = next(b[2] for b in spans if b[1] <= i < b[2])
    k = min(i + 1 + horizon, i1 - 1)
    if k <= i:
        return None
    return bars["c"][k] - row["entry"]


def welch(a, b):
    """(difference of means, t) for two independent samples, no scipy."""
    import math
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None, None
    ma = sum(a) / na
    mb = sum(b) / nb
    va = sum((x - ma) ** 2 for x in a) / (na - 1)
    vb = sum((x - mb) ** 2 for x in b) / (nb - 1)
    se = math.sqrt(va / na + vb / nb)
    if se <= 0:
        return ma - mb, None
    return ma - mb, (ma - mb) / se


def report(bars, spans, rows, label, horizon, pool):
    """Conditional vs unconditional forward move, with a Welch t."""
    sel = [r for r in rows if r.get(label)]
    vals = [forward(bars, spans, r, horizon) for r in sel]
    vals = [v for v in vals if v is not None]
    if not vals:
        return "  %-12s (no signals)" % label
    base = [forward(bars, spans, r, horizon) for r in pool]
    base = [v for v in base if v is not None]
    m = sum(vals) / len(vals)
    win = 100.0 * sum(1 for v in vals if v > 0) / len(vals)
    d, t = welch(vals, base)
    ts = ("%+.1f" % t) if t is not None else "n/a"
    return ("  %-12s n=%-5d mean %+7.1f  med %+7.1f  win %4.1f%%   "
            "vs all %+7.1f  (d %+6.1f, t %s)"
            % (label, len(vals), m, sorted(vals)[len(vals) // 2], win,
               (sum(base) / len(base)) if base else 0.0, d or 0.0, ts))


def main() -> None:
    from collections import Counter
    symbol = str(arg("--index", "NIFTY")).upper()
    start = str(arg("--start", "2025-01-05"))
    end = str(arg("--end", "2026-09-01"))
    print("fetching %s 5-min bars %s .. %s" % (symbol, start, end), flush=True)
    bars = fetch_bars(symbol, start, end, progress=lambda m: print(m, flush=True))
    spans = session_spans(bars)
    print("bars %d over %d sessions (%s .. %s)"
          % (len(bars["ts"]), len(spans), spans[0][0], spans[-1][0]), flush=True)

    rows = signals(bars, spans)
    print("\nscored bars: %d" % len(rows))
    print("regime mix:", dict(Counter(r["regime"] for r in rows)))

    for r in rows:
        r["bull_break"] = (r["regime"] == "BULL" and r["broke_up"]
                           and r["vol_ok"] and r["exp_ok"])
        r["bear_break"] = (r["regime"] == "BEAR" and r["broke_dn"]
                           and r["vol_ok"] and r["exp_ok"])
        r["bull_regime"] = r["regime"] == "BULL"
        r["bear_regime"] = r["regime"] == "BEAR"

    for horizon, name in ((6, "30 min"), (12, "60 min"), (999, "to the bell")):
        print("\n=== forward move, %s ===" % name)
        for lab in ("bull_regime", "bear_regime", "bull_break", "bear_break"):
            print(report(bars, spans, rows, lab, horizon, rows))


if __name__ == "__main__":
    main()
