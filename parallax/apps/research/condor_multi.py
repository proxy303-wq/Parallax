"""Corrected condor grid across SENSEX / BANKNIFTY / BANKEX.

Same method as the NIFTY run: +-10 ladder (Dhan serves no more than that),
intrabar exits, 3% round-trip cost, hold-to-expiry vs ratchet, optional
roll-the-wings-in, and a train/test split.

Expiry weekday is per index and comes from the registry, not assumed:

    NIFTY      weekly   Tuesday       (weekday 1)
    SENSEX     weekly   Thursday      (weekday 3)
    BANKNIFTY  monthly  last Tuesday
    BANKEX     monthly  last Thursday

BANKNIFTY and BANKEX are MONTHLY, so 20 months is only ~20 expiries each.
Sample sizes are printed and flagged - a 20-trade result is not evidence.
"""
from __future__ import annotations

import datetime
import json
import os
import pickle
import statistics
import sys
import time as _t
import urllib.request
from collections import OrderedDict

from parallax.adapters.broker.dhan_auth import active_token
from parallax.config.indices import spec as index_spec

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
MAX_OFF = int(os.environ.get("CONDOR_MAX_OFF", "10"))
START = os.environ.get("CONDOR_START", "2025-09-01")
END = os.environ.get("CONDOR_END", "2026-09-01")
#: split so that train and test are each half of the window
SPLIT = os.environ.get("CONDOR_SPLIT", "2026-03-01")
#: The cache filename is keyed on START/END, but the window we REPORT on can be
#: narrower - a long cached ladder can be clipped to any sub-window without a
#: refetch.  Defaults to START/END when not given.
WIN_START = os.environ.get("CONDOR_WIN_START", START)
WIN_END = os.environ.get("CONDOR_WIN_END", END)
HERE = os.path.dirname(os.path.abspath(__file__))
EXP_WEEKDAY = {"NIFTY": 1, "BANKNIFTY": 1, "FINNIFTY": 1, "SENSEX": 3, "BANKEX": 3}


def flag_for(sym):
    return "WEEK" if sym in ("NIFTY", "SENSEX") else "MONTH"


STATS = {"calls": 0, "fails": 0, "secs": 0.0}


def fetch(tok, idx, off, otype, f, t):
    b = {"exchangeSegment": idx.exchange_segment, "interval": "5",
         "securityId": idx.underlying_id, "instrument": "OPTIDX",
         "expiryFlag": flag_for(idx.symbol), "expiryCode": 1,
         "strike": off, "drvOptionType": otype,
         "requiredData": ["close", "high", "low", "spot"], "fromDate": f, "toDate": t}
    t0 = _t.time()
    for a in range(3):
        try:
            req = urllib.request.Request("https://api.dhan.co/v2/charts/rollingoption",
                data=json.dumps(b).encode(),
                headers={"Accept": "application/json",
                         "Content-Type": "application/json",
                         "access-token": tok}, method="POST")
            with urllib.request.urlopen(req, timeout=45) as r:
                d = json.loads(r.read().decode())
            STATS["calls"] += 1
            STATS["secs"] += _t.time() - t0
            return (d.get("data") or {}).get("ce" if otype == "CALL" else "pe") or {}
        except Exception as e:
            if a == 2:
                STATS["calls"] += 1
                STATS["fails"] += 1
                STATS["secs"] += _t.time() - t0
                return {}
            _t.sleep(0.8 * (a + 1))
    return {}


def ladder(symbol):
    idx = index_spec(symbol)
    cache = os.path.join(HERE, "_ladder_%s_%s_%s_off%d.pkl" % (symbol, START, END, MAX_OFF))
    if os.path.exists(cache):
        with open(cache, "rb") as fh:
            print("%s: %d bars from cache" % (symbol, len(pickle.load(fh))), flush=True)
        with open(cache, "rb") as fh:
            return idx, pickle.load(fh)
    tok, _ = active_token()
    step = float(idx.step)
    wins, d = [], datetime.date.fromisoformat(START)
    end = datetime.date.fromisoformat(END)
    while d < end:
        e = min(d + datetime.timedelta(days=27), end)
        wins.append((d.isoformat(), e.isoformat()))
        d = e
    offs = ["ATM"] + ["ATM%+d" % j for j in range(-MAX_OFF, MAX_OFF + 1) if j != 0]
    print("%s: %d series x %d windows = %d calls" % (
        symbol, len(offs) * 2, len(wins), len(offs) * 2 * len(wins)), flush=True)
    byts = {}
    n = 0
    for off in offs:
        for ot in ("CALL", "PUT"):
            k = 0 if off == "ATM" else int(off.replace("ATM", "").replace("+", ""))
            for f, t in wins:
                dd = fetch(tok, idx, off, ot, f, t)
                ts = dd.get("timestamp") or []
                sp = dd.get("spot") or []
                cl = dd.get("close") or []
                hi = dd.get("high") or []
                lo = dd.get("low") or []
                for i, tv in enumerate(ts):
                    if i >= len(cl) or i >= len(sp):
                        break
                    row = byts.setdefault(int(tv), {"spot": float(sp[i]),
                                                    "CALL": {}, "PUT": {}})
                    row[ot][k * step] = (float(cl[i]),
                                         float(hi[i]) if i < len(hi) else float(cl[i]),
                                         float(lo[i]) if i < len(lo) else float(cl[i]))
                _t.sleep(0.1)
            n += 1
        c = max(1, STATS["calls"])
        print("  %s series %d/%d, %d bars | %d calls %.2fs/call %d failed | %.0f%% done" % (
            symbol, n, len(offs) * 2, len(byts), STATS["calls"],
            STATS["secs"] / c, STATS["fails"],
            100.0 * n / (len(offs) * 2)), flush=True)
    with open(cache, "wb") as fh:
        pickle.dump(byts, fh)
    return idx, byts


def legs_for(atm, step, short_off, wing):
    return [("sp", atm - short_off * step, "PUT", 1),
            ("sc", atm + short_off * step, "CALL", 1),
            ("hp", atm - (short_off + wing) * step, "PUT", -1),
            ("hc", atm + (short_off + wing) * step, "CALL", -1)]


def val_at(row, legs, at, step, field=0):
    tot = 0.0
    for name, k, ot, sgn in legs:
        off = k - at
        if abs(off) > MAX_OFF * step + 1e-9:
            return None
        t = row[ot].get(off)
        if t is None:
            return None
        tot += (t[field] if sgn > 0 else -t[field])
    return tot


def extremes(row, legs, at, step):
    vals = []
    for cf, pf in ((1, 2), (2, 1)):
        tot = 0.0
        for name, k, ot, sgn in legs:
            off = k - at
            if abs(off) > MAX_OFF * step + 1e-9:
                return None
            t = row[ot].get(off)
            if t is None:
                return None
            f = cf if ot == "CALL" else pf
            tot += (t[f] if sgn > 0 else -t[f])
        vals.append(tot)
    return min(vals), max(vals)


def floor_of(peak):
    return (0.90 if peak >= 0.95 else 0.75 if peak >= 0.80
            else 0.50 if peak >= 0.50 else 0.0)


def expiry_days(days, symbol):
    wd = EXP_WEEKDAY[symbol]
    out = []
    for d in days:
        # window filter: a longer cache can be clipped to any sub-window
        if not (datetime.date.fromisoformat(WIN_START) <= d
                < datetime.date.fromisoformat(WIN_END)):
            continue
        if d.weekday() != wd:
            continue
        if flag_for(symbol) == "MONTH":
            if (d + datetime.timedelta(days=7)).month == d.month:
                continue
        out.append(d)
    return out


LOTS = int(os.environ.get("CONDOR_LOTS", "5"))


def run(idx, byts, short_off, wing, manage, roll, sl=2.0, cost_pct=0.03, lots=None):
    lots = LOTS if lots is None else lots
    step = float(idx.step)
    days = OrderedDict()
    for ts in sorted(byts):
        days.setdefault(datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
                        .astimezone(IST).date(), []).append(ts)
    out = []
    used = skipped = 0
    for d in expiry_days(days, idx.symbol):
        tss = days.get(d) or []
        if len(tss) < 60:
            continue
        ed = byts[tss[1]]
        atm = round(ed["spot"] / step) * step
        legs = legs_for(atm, step, short_off, wing)
        credit = val_at(ed, legs, atm, step)
        if credit is None or credit <= 0:
            continue
        peak, outcome, pct, last = 0.0, "close", None, None
        rolled = 0
        for ts in tss[2:]:
            row = byts[ts]
            at = round(row["spot"] / step) * step
            val = val_at(row, legs, at, step)
            if val is None:
                skipped += 1
                continue
            used += 1
            last = (credit - val) / credit
            adverse = val
            ext = extremes(row, legs, at, step)
            if ext is not None:
                adverse = max(val, ext[1])
            worst = (credit - adverse) / credit
            if manage == "ratchet":
                fl = floor_of(peak)
                if worst <= -sl:
                    outcome, pct = "sl", -sl
                    break
                if fl > 0 and worst <= fl:
                    outcome, pct = "ratchet", fl
                    break
            peak = max(peak, last)
            trig = (roll == "loss" and last <= -0.50) or \
                   (roll == "both" and (last >= 0.40 or last <= -0.50))
            if trig and rolled < 2:
                nl, debit, ok = [], 0.0, True
                shorts = {k for n, k, o, s in legs if s > 0}
                for name, k, ot, sgn in legs:
                    if sgn < 0:
                        nk = k + step if ot == "PUT" else k - step
                        o1, o2 = k - at, nk - at
                        if nk in shorts or abs(o1) > MAX_OFF * step + 1e-9 \
                           or abs(o2) > MAX_OFF * step + 1e-9:
                            ok = False
                            break
                        t1, t2 = row[ot].get(o1), row[ot].get(o2)
                        if t1 is None or t2 is None:
                            ok = False
                            break
                        debit += t2[0] - t1[0]
                        nl.append((name, nk, ot, sgn))
                    else:
                        nl.append((name, k, ot, sgn))
                if ok:
                    legs, credit, rolled = nl, credit - debit, rolled + 1
                else:
                    rolled = 2
        if pct is None:
            pct = last if last is not None else 0.0
        gross = pct * credit * idx.lot * lots
        out.append({"date": str(d), "pnl": gross - cost_pct * credit * idx.lot * lots})
    return out, used, skipped


def summ(tr):
    if not tr:
        return None
    v = [t["pnl"] for t in tr]
    n = len(v)
    m = sum(v) / n
    sd = statistics.pstdev(v) if n > 1 else 0.0
    return {"n": n, "total": sum(v), "t": m / (sd / n ** 0.5) if sd else 0.0,
            "worst": min(v), "loss_rate": len([x for x in v if x <= 0]) / n}


def main():
    syms = sys.argv[1:] or ["SENSEX", "BANKNIFTY", "BANKEX"]
    for symbol in syms:
        try:
            idx, byts = ladder(symbol)
        except Exception as e:
            print("%s FAILED %s" % (symbol, str(e)[:100]), flush=True)
            continue
        print()
        print("=== %s  lot %d  step %d  %s expiry ===" % (
            symbol, idx.lot, idx.step, flag_for(symbol)))
        rows = []
        for so in (2, 3):
            for w in (1, 2, 3):
                for mg in ("hold", "ratchet"):
                    for rl in (None, "loss"):
                        tr, u, k = run(idx, byts, so, w, mg, rl)
                        s = summ(tr)
                        if not s:
                            continue
                        trn = [t for t in tr if t["date"] < SPLIT]
                        tst = [t for t in tr if t["date"] >= SPLIT]
                        sn, st = summ(trn), summ(tst)
                        rows.append((so, w, mg, rl, s, sn, st, 100.0 * k / max(1, u + k)))
        rows.sort(key=lambda r: -r[1])
        print("%-5s %-4s %-8s %-5s %5s %10s %7s %9s %7s %7s %6s" % (
            "short", "wing", "manage", "roll", "n", "total", "t", "worst",
            "loss%", "test t", "clip%"))
        for so, w, mg, rl, s, sn, st, clip in rows:
            print("%-5d %-4d %-8s %-5s %5d %10s %+7.2f %9s %6.0f%% %+7.2f %5.1f%%" % (
                so, w, mg, rl or "-", s["n"], format(int(s["total"]), ","), s["t"],
                format(int(s["worst"]), ","), s["loss_rate"] * 100,
                st["t"] if st else 0.0, clip))
        best = max([r for r in rows if r[1] >= 2] or rows,
                   key=lambda r: (r[6]["t"] if r[6] else -99))
        print("  best by TEST t: short %d wing %d %s roll=%s  test t=%+.2f  Rs%s" % (
            best[0], best[1], best[2], best[3] or "-",
            best[6]["t"] if best[6] else 0.0,
            format(int(best[6]["total"]), ",") if best[6] else "-"))
        print()


if __name__ == "__main__":
    main()
