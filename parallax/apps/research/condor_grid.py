"""Grid over the hold-to-expiry condor: can the loss tail be engineered away?

The trade that ran on 21-22 Sep was not the ratchet strategy.  It was:

    NIFTY iron condor, shorts ATM +- 2 strikes, hedges ATM +- 4 strikes
    8 lots, NO take-profit, NO stop, held to the expiry close
    credit 37.75, exit 9.75, +Rs 14,560

That is the configuration gridded here.  Three levers:

    short_off   how far the SOLD legs sit from ATM (1..3 strikes)
    wing        how far the BOUGHT legs sit beyond the sold ones (1..3)
    roll        whether the bought legs are pulled in, and when

Wing width is the direct lever on the tail: max loss = (wing - credit) and
credit does NOT fall as fast as the wing, because a closer hedge is worth
more than it costs to buy.  The article is right about that much.

Everything is reported on TRAIN (<2026-01-01) and TEST (>=2026-01-01)
separately.  A cell that only wins in one half is noise, however good it looks.
"""
from __future__ import annotations

import datetime
import os
import pickle
import statistics
import sys
from collections import OrderedDict

from parallax.config.indices import spec as index_spec
from parallax.apps.research.condor_study import _floor

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
HERE = os.path.dirname(os.path.abspath(__file__))
# Ladder depth.  Dhan serves ATM+-10 at most (ATM+-12 returns 0 rows), and the
# depth bounds which shapes can be judged: a limb reaching R strikes out loses
# its mark on any move beyond (MAX_OFF - R) strikes, and those are the moved
# bars.  Set CONDOR_MAX_OFF=10 to use the deeper rollout.
MAX_OFF = int(os.environ.get("CONDOR_MAX_OFF", "8"))


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
    for call_f, put_f in ((1, 2), (2, 1)):
        tot = 0.0
        for name, k, ot, sgn in legs:
            off = k - at
            if abs(off) > MAX_OFF * step + 1e-9:
                return None
            t = row[ot].get(off)
            if t is None:
                return None
            f = call_f if ot == "CALL" else put_f
            tot += (t[f] if sgn > 0 else -t[f])
        vals.append(tot)
    return min(vals), max(vals)


def run_byts(idx, byts, short_off, wing, manage, roll, sl, cost_pct, lots):
    step = float(idx.step)
    days = OrderedDict()
    for ts in sorted(byts):
        days.setdefault(datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
                        .astimezone(IST).date(), []).append(ts)
    exp = [d for d in days if d.weekday() == 1]
    out = []
    used = skipped = 0
    for d in exp:
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
                # THE LADDER IS ONLY +-8 STRIKES.  A leg that wanders past that
                # loses its mark and the bar is dropped - and those are exactly
                # the bars where the market moved, i.e. the adverse ones.  A
                # config sitting far out loses its worst bars and then reports
                # the profit from before the move.  Count them.
                skipped += 1
                continue
            used += 1
            last = (credit - val) / credit
            adverse = val
            ext = extremes(row, legs, at, step)
            if ext is not None:
                adverse = max(val, ext[1])
            prof_worst = (credit - adverse) / credit
            if manage == "ratchet":
                floor = _floor(peak)
                if prof_worst <= -sl:
                    outcome, pct = "sl", -sl
                    break
                if floor > 0 and prof_worst <= floor:
                    outcome, pct = "ratchet", floor
                    break
            peak = max(peak, last)
            trigger = (roll == "up" and last >= 0.40) or \
                      (roll == "loss" and last <= -0.50) or \
                      (roll == "both" and (last >= 0.40 or last <= -0.50))
            if trigger and rolled < 2:
                new_legs, debit, ok = [], 0.0, True
                shorts = {k for n, k, o, s in legs if s > 0}
                for name, k, ot, sgn in legs:
                    if sgn < 0:
                        nk = k + step if ot == "PUT" else k - step
                        if nk in shorts:
                            ok = False
                            break
                        o1, o2 = k - at, nk - at
                        if abs(o1) > MAX_OFF * step + 1e-9 or abs(o2) > MAX_OFF * step + 1e-9:
                            ok = False
                            break
                        t1, t2 = row[ot].get(o1), row[ot].get(o2)
                        if t1 is None or t2 is None:
                            ok = False
                            break
                        debit += t2[0] - t1[0]
                        new_legs.append((name, nk, ot, sgn))
                    else:
                        new_legs.append((name, k, ot, sgn))
                if ok:
                    legs, credit, rolled = new_legs, credit - debit, rolled + 1
                else:
                    rolled = 2
        if pct is None:
            pct = last if last is not None else 0.0
        gross = pct * credit * idx.lot * lots
        out.append({"date": str(d), "credit": credit, "outcome": outcome,
                    "pnl": gross - cost_pct * credit * idx.lot * lots})
    return out, used, skipped


def summarize(tr):
    if not tr:
        return None
    v = [t["pnl"] for t in tr]
    n = len(v)
    mean = sum(v) / n
    sd = statistics.pstdev(v) if n > 1 else 0.0
    losses = [x for x in v if x <= 0]
    worst = min(v)
    worst_credit = min(t["credit"] for t in tr)
    return {"n": n, "total": sum(v), "mean": mean,
            "t": mean / (sd / n ** 0.5) if sd else 0.0,
            "worst": worst, "n_loss": len(losses),
            "loss_rate": len(losses) / n, "min_credit": worst_credit}


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    split = "2026-01-01"
    idx = index_spec(symbol)
    suffix = "" if MAX_OFF == 8 else "_off%d" % MAX_OFF
    cache = os.path.join(HERE, "_ladder_%s_2025-01-05_2026-09-01%s.pkl" % (symbol, suffix))
    with open(cache, "rb") as fh:
        byts = pickle.load(fh)
    depth = max(len(r["CALL"]) for r in list(byts.values())[:500])
    print("ladder: %d bars, lot %d, step %d, MAX_OFF %d (max strikes present %d)" % (
        len(byts), idx.lot, idx.step, MAX_OFF, depth))
    print()
    print("a move of M strikes is covered while M <= MAX_OFF - R  (R = strikes out)")
    print()
    print("=== LADDER COVERAGE (ladder is only +-8 strikes from the bar ATM) ===")
    print("%-6s %-5s %8s %10s   %s" % (
        "short", "wing", "used", "clipped", "legs reach (strikes out)"))
    for short_off in (2, 3, 4):
        for wing in (1, 2, 3, 4, 6):
            _, u, k = run_byts(idx, byts, short_off, wing, "hold", None, 2.0, 0.03, 5)
            print("%-6d %-5d %8d %9.1f%%   %d" % (
                short_off, wing, u, 100.0 * k / max(1, u + k), short_off + wing))
    print()
    print("%-6s %-5s %-9s %-5s %6s %10s %7s %9s %8s %8s" % (
        "short", "wing", "manage", "roll", "n", "total", "t", "worst",
        "loss%", "min cred"))
    results = []
    for short_off in (2, 3, 4):
        for wing in (1, 2, 3, 4, 6):
            for manage in ("hold", "ratchet"):
                for roll in (None, "loss", "both"):
                    tr, _u, _k = run_byts(idx, byts, short_off, wing, manage, roll,
                                          2.0, 0.03, 4)
                    s = summarize(tr)
                    if not s:
                        continue
                    trn = [t for t in tr if t["date"] < split]
                    tst = [t for t in tr if t["date"] >= split]
                    sn, st = summarize(trn), summarize(tst)
                    results.append((short_off, wing, manage, roll, s, sn, st))
                    print("%-6d %-5d %-9s %-5s %6d %10s %+7.2f %9s %7.0f%% %8.1f" % (
                        short_off, wing, manage, roll or "-", s["n"],
                        format(int(s["total"]), ","), s["t"],
                        format(int(s["worst"]), ","), s["loss_rate"] * 100,
                        s["min_credit"]))
    print()
    print("=== rank by TEST t-stat (the only half that counts as out of sample) ===")
    print("%-6s %-5s %-9s %-5s %8s %8s %8s" % (
        "short", "wing", "manage", "roll", "train t", "test t", "test Rs"))
    for r in sorted(results, key=lambda x: -(x[6]["t"] if x[6] else -99))[:12]:
        so, w, m, rl, s, sn, st = r
        print("%-6d %-5d %-9s %-5s %+8.2f %+8.2f %8s" % (
            so, w, m, rl or "-", sn["t"] if sn else 0.0, st["t"] if st else 0.0,
            format(int(st["total"]), ",") if st else "-"))


if __name__ == "__main__":
    main()
