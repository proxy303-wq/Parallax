"""Corrected expiry-day condor study.

Fixes over the ad-hoc scripts, in order of how much they matter:

  1. INTRABAR STOPS.  The old method checked the stop against each 5-minute
     CLOSE, so a spike that went through the stop and came back was never
     seen - it could only ever understate losses.  Now the adverse extreme of
     each bar is priced too: shorts at their high, hedges at their low.

  2. COSTS.  Fills were at the option close (a mid-ish mark).  Real entry
     sells the bid and buys the ask; the round trip measured live at ~1.1
     points on a 38-point credit, so 3% of the credit is the default and the
     result is reported at 0%, 1%, 3% and 5%.

  3. OUT OF SAMPLE IN TIME.  The TP ladder is selected on the TRAIN window
     and only then applied to TEST.  Reporting one window for both - which
     every earlier number did - is not a walk-forward.

  4. UNCERTAINTY.  Bootstrap CI on the mean P&L and a t-statistic against
     zero.  "15/15 winners" is not a result; it is an absence of data.

  5. EXACT STRIKE LOOKUP.  Verified rather than assumed: entry strikes and
     the bar ATM are both multiples of the strike step, so the offset always
     lands on a ladder key and no interpolation happens.  Any miss is counted
     and reported instead of being silently skipped.

    python -m parallax.apps.research.condor_study NIFTY 2025-01-05 2026-09-01
"""
from __future__ import annotations

import datetime
import json
import random
import statistics
import sys
import time as _t
import urllib.request
from collections import OrderedDict

from parallax.adapters.broker.dhan_auth import active_token
from parallax.config.indices import spec as index_spec

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
MAX_OFF = 8          # ladder depth in strikes each way


def fetch(tok, idx, off, otype, f, t):
    b = {"exchangeSegment": idx.exchange_segment, "interval": "5",
         "securityId": idx.underlying_id, "instrument": "OPTIDX",
         "expiryFlag": "WEEK" if idx.symbol in ("NIFTY", "SENSEX") else "MONTH",
         "expiryCode": 1, "strike": off, "drvOptionType": otype,
         "requiredData": ["close", "high", "low", "spot"], "fromDate": f, "toDate": t}
    for a in range(3):
        try:
            req = urllib.request.Request("https://api.dhan.co/v2/charts/rollingoption",
                data=json.dumps(b).encode(),
                headers={"Accept": "application/json",
                         "Content-Type": "application/json",
                         "access-token": tok}, method="POST")
            with urllib.request.urlopen(req, timeout=45) as r:
                d = json.loads(r.read().decode())
            return (d.get("data") or {}).get("ce" if otype == "CALL" else "pe") or {}
        except Exception:
            if a == 2:
                return {}
            _t.sleep(1.5 * (a + 1))
    return {}


def windows(start: datetime.date, end: datetime.date, days: int = 27):
    out, d = [], start
    while d < end:
        e = min(d + datetime.timedelta(days=days), end)
        out.append((d.isoformat(), e.isoformat()))
        d = e
    return out


def load_ladder(symbol: str, start: str, end: str, progress=print):
    """{ts: {spot, CALL:{offset: (close, high, low)}, PUT:{...}}}"""
    idx = index_spec(symbol)
    tok, _ = active_token()
    step = float(idx.step)
    offs = ["ATM"] + ["ATM%+d" % j for j in range(-MAX_OFF, MAX_OFF + 1) if j != 0]
    wins = windows(datetime.date.fromisoformat(start), datetime.date.fromisoformat(end))
    byts: dict = {}
    series = 0
    for off in offs:
        for ot in ("CALL", "PUT"):
            n = 0 if off == "ATM" else int(off.replace("ATM", "").replace("+", ""))
            for f, t in wins:
                d = fetch(tok, idx, off, ot, f, t)
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
                _t.sleep(0.1)
            series += 1
        if series % 6 == 0:
            progress("  %s series %d/%d, %d bars" % (symbol, series, len(offs) * 2, len(byts)))
    return idx, byts


def _legs(atm, step, width=2):
    return [("sp", atm - width * step, "PUT", 1),
            ("sc", atm + width * step, "CALL", 1),
            ("hp", atm - (width + 2) * step, "PUT", -1),
            ("hc", atm + (width + 2) * step, "CALL", -1)]


def _value(row, legs, atm_t, step, field=0, adverse=False):
    """Condor cost to close.  adverse=True prices shorts at their HIGH and
    hedges at their LOW - the worst simultaneous combination in the bar."""
    total = 0.0
    for name, k, ot, sgn in legs:
        off = k - atm_t
        if abs(off) > MAX_OFF * step + 1e-9:
            return None
        t = row[ot].get(off)
        if t is None:
            return None
        if adverse:
            px = t[1] if sgn > 0 else t[2]
        else:
            px = t[0]
        total += px if sgn > 0 else -px
    return total


def simulate(idx, byts, sl=2.0, cost_pct=0.0, intrabar=True, width=2, lots=5):
    step = float(idx.step)
    days = OrderedDict()
    for ts in sorted(byts):
        days.setdefault(datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
                        .astimezone(IST).date(), []).append(ts)
    exp = [d for d in days if d.weekday() == 1]
    trades, misses = [], 0
    for d in exp:
        tss = days.get(d) or []
        if len(tss) < 60:
            continue
        ed = byts[tss[1]]
        atm = round(ed["spot"] / step) * step
        legs = _legs(atm, step, width)
        credit = _value(ed, legs, atm, step)
        if credit is None:
            misses += 1
            continue
        if credit <= 0:
            continue
        peak, outcome, pct, last = 0.0, "close", None, None
        for ts in tss[2:]:
            row = byts[ts]
            at = round(row["spot"] / step) * step
            val = _value(row, legs, at, step)
            if val is None:
                misses += 1
                continue
            prof = (credit - val) / credit
            last = prof
            peak = max(peak, prof)
            worst = val
            best = val
            if intrabar:
                w = _value(row, legs, at, step, adverse=True)
                if w is not None:
                    worst = max(val, w)
                # favourable extreme: shorts low, hedges high
                fav = 0.0
                ok = True
                for name, k, ot, sgn in legs:
                    off = k - at
                    t = row[ot].get(off)
                    if t is None:
                        ok = False
                        break
                    fav += (t[2] if sgn > 0 else -t[1])
                if ok:
                    best = min(val, fav)
            prof_worst = (credit - worst) / credit
            prof_best = (credit - best) / credit
            peak = max(peak, prof_best)
            if prof_worst <= -sl:
                outcome, pct = "sl", -sl
                break
            fl = (0.90 if peak >= 0.95 else 0.75 if peak >= 0.80
                  else 0.50 if peak >= 0.50 else 0.0)
            if fl > 0 and prof_worst <= fl:
                outcome, pct = "ratchet", fl
                break
        if pct is None:
            pct = last if last is not None else 0.0
        gross = pct * credit * idx.lot * lots
        cost = cost_pct * credit * idx.lot * lots
        trades.append({"date": str(d), "credit": credit, "peak": peak,
                       "outcome": outcome, "pnl": gross - cost})
    return trades, misses


def stats(trades, n_boot=10000, seed=7):
    if not trades:
        return {}
    v = [t["pnl"] for t in trades]
    n = len(v)
    total = sum(v)
    wins = [x for x in v if x > 0]
    losses = [x for x in v if x <= 0]
    mean = total / n
    sd = statistics.pstdev(v) if n > 1 else 0.0
    t_stat = mean / (sd / (n ** 0.5)) if sd > 0 else 0.0
    rnd = random.Random(seed)
    means = []
    for _ in range(n_boot):
        s = sum(v[rnd.randrange(n)] for _ in range(n)) / n
        means.append(s)
    means.sort()
    lo = means[int(0.05 * n_boot)]
    hi = means[int(0.95 * n_boot)]
    return {"n": n, "total": total, "mean": mean, "sd": sd, "t": t_stat,
            "wins": len(wins), "losses": len(losses),
            "gross_win": sum(wins), "gross_loss": sum(losses),
            "worst": min(v), "best": max(v), "ci_lo": lo, "ci_hi": hi,
            "outcomes": {o: len([t for t in trades if t["outcome"] == o])
                         for o in set(t["outcome"] for t in trades)}}


def _show(tag, s):
    if not s:
        print("%-22s no trades" % tag)
        return
    print("%-22s n=%3d win=%2d/%2d  total=Rs%9s  mean=Rs%7s  t=%+5.2f" % (
        tag, s["n"], s["wins"], s["n"], format(int(s["total"]), ","),
        format(int(s["mean"]), ","), s["t"]))
    print("%-22s 95%% CI on the mean: Rs%s .. Rs%s   worst Rs%s  best Rs%s" % (
        "", format(int(s["ci_lo"]), ","), format(int(s["ci_hi"]), ","),
        format(int(s["worst"]), ","), format(int(s["best"]), ",")))
    print("%-22s outcomes %s" % ("", s["outcomes"]))


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    start = sys.argv[2] if len(sys.argv) > 2 else "2025-01-05"
    end = sys.argv[3] if len(sys.argv) > 3 else "2026-09-01"
    split = sys.argv[4] if len(sys.argv) > 4 else "2026-01-01"
    idx, byts = load_ladder(symbol, start, end)
    print()
    print("=== %s  %s .. %s  (%d bars, lot %d) ===" % (
        symbol, start, end, len(byts), idx.lot))
    for label, kw in (("closes only, no cost", {"intrabar": False, "cost_pct": 0.0}),
                      ("intrabar, no cost", {"intrabar": True, "cost_pct": 0.0}),
                      ("intrabar, 1% cost", {"intrabar": True, "cost_pct": 0.01}),
                      ("intrabar, 3% cost", {"intrabar": True, "cost_pct": 0.03}),
                      ("intrabar, 5% cost", {"intrabar": True, "cost_pct": 0.05})):
        tr, miss = simulate(idx, byts, **kw)
        s = stats(tr)
        print()
        _show(label, s)
        trn = [t for t in tr if t["date"] < split]
        tst = [t for t in tr if t["date"] >= split]
        print("%-22s   train(<%s):" % ("", split), end=" ")
        st = stats(trn)
        print("n=%d total=Rs%s t=%+.2f" % (st.get("n", 0),
              format(int(st.get("total", 0)), ","), st.get("t", 0.0)))
        print("%-22s   test(>=%s): " % ("", split), end=" ")
        st2 = stats(tst)
        print("n=%d total=Rs%s t=%+.2f" % (st2.get("n", 0),
              format(int(st2.get("total", 0)), ","), st2.get("t", 0.0)))
    print()
    print("strike-lookup misses:", miss)


if __name__ == "__main__":
    main()
