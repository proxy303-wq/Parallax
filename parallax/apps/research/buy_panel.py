"""A fixed-expiry option panel, stitched from rolling expiry codes.

THE PROBLEM THIS SOLVES.  Dhan's rolling-option endpoint is ATM-relative and it
rolls by itself: "expiryCode 3" always means whatever is third-nearest TODAY.
So one code's series does not follow one contract - it ages from 3rd to 2nd to
1st and then dies.  Both of the buying tests built before this used a single
code, which means a position held across a weekly rollover silently had the
instrument swapped underneath it, and the whole exercise was really measuring
the front week (0-6 days), the steepest part of the theta curve.

Here one expiry is followed through its whole listed life by reading whichever
code it currently occupies each day: code 4 while it is 4th nearest, then 3, 2,
1.  That is what makes a 20-40 DTE position measurable at all.

Also here: the fixed-strike rule.  A strike must be read at its DRIFTED offset
(strike minus the current atm), never at its entry offset, or the position is
silently re-struck to the new ATM every bar.  That mistake already cost one
study a t-statistic of -21 that meant nothing.

Usage:
    python -m parallax.apps.research.buy_panel --index NIFTY
"""
from __future__ import annotations

import datetime
import os
import sys

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
HERE = os.path.dirname(os.path.abspath(__file__))


def arg(flag, default):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _ist(ts):
    return datetime.datetime.fromtimestamp(int(ts), IST)


def code_for(expiries, day, target):
    """Which expiryCode the target expiry occupies on a day (1 = nearest)."""
    fut = [e for e in expiries if e >= day]
    if target not in fut:
        return None
    return fut.index(target) + 1


def stitch(ladders, expiries, target):
    """One expiry's ATM-relative series across its whole listed life."""
    bycode = {}
    for code, lad in ladders.items():
        per_day = {}
        for ts in sorted(lad):
            per_day.setdefault(_ist(ts).date(), []).append(ts)
        bycode[int(code)] = (lad, per_day)
    out = {}
    for day in sorted({d for _, pd in bycode.values() for d in pd}):
        if day > target:
            continue
        c = code_for(expiries, day, target)
        if c is None or c not in bycode:
            continue
        lad, per_day = bycode[c]
        for ts in per_day.get(day, []):
            out[ts] = lad[ts]
    return out


def rows_by_day(panel):
    out = {}
    for ts in sorted(panel):
        out.setdefault(_ist(ts).date(), []).append(ts)
    return out


def atm_of(row, step):
    return round(float(row["spot"]) / step) * step


def leg_value(row, legs, step):
    """Signed value of FIXED strikes, read at their drifted offsets."""
    atm = atm_of(row, step)
    tot = 0.0
    for strike, ot, sgn in legs:
        key = round((strike - atm) / step) * step
        t = (row.get(ot) or {}).get(key)
        if t is None:
            return None
        tot += sgn * float(t[0])
    return tot


def load_ladders(symbol, codes=(1, 2, 3, 4), start="2025-01-05",
                 end="2026-09-01", progress=print):
    """Every code we hold a cache for; a missing one just narrows the window."""
    import pickle
    out = {}
    for c in codes:
        cands = ["_ladder_%s_C%s_%s_%s.pkl" % (symbol, c, start, end)]
        if c == 1:
            cands.append("_ladder_%s_%s_%s_off10.pkl" % (symbol, start, end))
            cands.append("_ladder_%s_%s_%s.pkl" % (symbol, start, end))
        if c == 2:
            cands.append("_ladder_%s_NEXT_%s_%s.pkl" % (symbol, start, end))
        for name in cands:
            path = os.path.join(HERE, name)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "rb") as fh:
                    out[c] = pickle.load(fh)
                progress("  code %s <- %s (%d bars)"
                         % (c, name, len(out[c])))
            except Exception as e:
                progress("  code %s unreadable: %s" % (c, e))
            break
    return out


def expiry_days(sessions, weekday=1):
    """NIFTY expires on Tuesdays; using real sessions skips holidays."""
    return [d for d in sessions if d.weekday() == weekday]


def straddle(row):
    c = (row.get("CALL") or {}).get(0.0)
    p = (row.get("PUT") or {}).get(0.0)
    if not c or not p:
        return None
    return float(c[0]) + float(p[0])


def _target_expiry(expiries, day, min_dte, max_dte):
    for e in expiries:
        dte = (e - day).days
        if dte < min_dte:
            continue
        if dte > max_dte:
            return None
        return e
    return None


def run(symbol="NIFTY", codes=(1, 2, 3, 4), min_dte=20, max_dte=45,
        kinds=("call", "put"), widths=(2, 4, 5), hold=5, entry_local=1,
        cost_pct=0.005, start="2025-01-05", end="2026-09-01", split=None,
        progress=print):
    """Buy a debit spread on an expiry that still has TIME, and hold it days.

    This is the rebuild.  The first buying study entered the front-week contract
    (0-6 DTE) and left the same session, which is the buyer's worst possible
    seat: maximum theta, no time for the move to arrive.  Here the expiry is
    chosen to sit min_dte..max_dte out, and the position is held hold sessions.
    """
    import math
    from parallax.apps.research.buy_signal import fetch_bars, session_spans
    from parallax.config.indices import spec
    step = float(spec(symbol).step)

    bars = fetch_bars(symbol, start, end, progress=lambda m: None)
    spans = session_spans(bars)
    sessions = [d for d, _, _ in spans]
    expiries = expiry_days(sessions, 1 if symbol in ("NIFTY", "BANKNIFTY",
                                                     "FINNIFTY") else 3)
    ladders = load_ladders(symbol, codes, start, end, progress)
    if not ladders:
        raise SystemExit("no ladders cached for %s" % symbol)
    progress("  %d expiries, %d sessions, codes %s"
             % (len(expiries), len(spans), sorted(ladders)))

    panels = {}

    def panel_for(target):
        if target not in panels:
            p = stitch(ladders, expiries, target)
            panels[target] = (p, rows_by_day(p))
        return panels[target]

    out, dtes, tried, dropped = {}, [], 0, 0
    for i, (day, i0, i1) in enumerate(spans):
        j = i + hold
        if j >= len(spans):
            continue
        tgt = _target_expiry(expiries, day, min_dte, max_dte)
        if tgt is None:
            continue
        tried += 1
        p, pday = panel_for(tgt)
        ins = pday.get(day) or []
        outs = pday.get(spans[j][0]) or []
        if not ins or not outs:
            continue
        t_in = ins[min(entry_local, len(ins) - 1)]
        t_out = outs[-1]
        rin, rout = p[t_in], p[t_out]
        atm = atm_of(rin, step)

        # EVERY VARIANT IS MEASURED ON ONE IDENTICAL SAMPLE, or an entry is
        # dropped for all of them.  This is not tidiness, it is the difference
        # between a result and an artefact: the ladder only reaches +-10 strikes,
        # and a held position drifts toward that ceiling, so the leg that leaves
        # the window is dropped - and it is a DIFFERENT leg in each direction.
        # Measured here: on the 48 entries where only the put spread resolved the
        # index fell 344 points on average, and on the 45 where only the call
        # spread resolved it rose 367.  Per-variant dropping therefore kept the
        # big down days for the puts and threw away the big up days, and reported
        # a +21.7 point "edge" that was pure selection.
        vals, ok = {}, True
        for kind in kinds:
            ot = "CALL" if kind == "call" else "PUT"
            sgn = 1.0 if kind == "call" else -1.0
            for w in widths:
                legs = [(atm, ot, 1.0), (atm + sgn * w * step, ot, -1.0)]
                vi = leg_value(rin, legs, step)
                vo = leg_value(rout, legs, step)
                if vi is None or vo is None or vi <= 0:
                    ok = False
                    break
                vals[(kind, w)] = (vi, vo)
            if not ok:
                break
        if not ok:
            dropped += 1
            continue
        dtes.append((tgt - day).days)
        half = "all" if split is None else ("train" if day < split else "test")
        for (kind, w), (vi, vo) in vals.items():
            debit = vi * (1.0 + cost_pct)
            pnl = vo * (1.0 - cost_pct) - debit
            out.setdefault((kind, w, half), []).append((pnl, debit))
    if dtes:
        progress("  %d entry days tried, %d measured, %d dropped off the ladder "
                 "ceiling, entry DTE %.0f-%.0f (med %.0f)"
                 % (tried, len(dtes), dropped, min(dtes), max(dtes),
                    sorted(dtes)[len(dtes) // 2]))
    return out


ATM_KINDS = ("call", "put", "straddle")


def _atm_legs(kind, atm):
    if kind == "call":
        return [(atm, "CALL", 1.0)]
    if kind == "put":
        return [(atm, "PUT", 1.0)]
    return [(atm, "CALL", 1.0), (atm, "PUT", 1.0)]


def run_atm(symbol="NIFTY", codes=(1, 2, 3, 4), min_dte=20, max_dte=45,
            kinds=ATM_KINDS, hold=2, entry_local=1, cost_pct=0.005,
            start="2025-01-05", end="2026-09-01", split=None, progress=print):
    """Buy the ATM option outright and hold it.

    WHY ATM-ONLY IS THE RIGHT MEASUREMENT HERE.  A fixed strike's offset in the
    ladder drifts by the index move, and the ladder reaches only +-10 strikes.  A
    SPREAD therefore loses whichever leg leaves the window first, and that is a
    different leg in each direction: a call spread drops on big DOWN days and a
    put spread on big UP days.  Measured, that kept the losing tail for one side
    and threw it away for the other, which is how a +21.7 point "put edge" got
    invented out of nothing.

    An ATM-only position holds a single leg at offset 0, so the only constraint
    is |move| <= 10 strikes in EITHER direction.  Symmetric, and twice the room -
    which matters because a long option's entire payoff lives in the tail that
    the spread was busy discarding.
    """
    from parallax.apps.research.buy_signal import fetch_bars, session_spans
    from parallax.config.indices import spec
    step = float(spec(symbol).step)

    bars = fetch_bars(symbol, start, end, progress=lambda m: None)
    spans = session_spans(bars)
    sessions = [d for d, _, _ in spans]
    expiries = expiry_days(sessions, 1 if symbol in ("NIFTY", "BANKNIFTY",
                                                     "FINNIFTY") else 3)
    ladders = load_ladders(symbol, codes, start, end, progress)
    if not ladders:
        raise SystemExit("no ladders cached for %s" % symbol)

    panels = {}

    def panel_for(target):
        if target not in panels:
            p = stitch(ladders, expiries, target)
            panels[target] = (p, rows_by_day(p))
        return panels[target]

    out, dtes, tried, dropped = {}, [], 0, 0
    for i, (day, i0, i1) in enumerate(spans):
        j = i + hold
        if j >= len(spans):
            continue
        tgt = _target_expiry(expiries, day, min_dte, max_dte)
        if tgt is None:
            continue
        tried += 1
        p, pday = panel_for(tgt)
        ins = pday.get(day) or []
        outs = pday.get(spans[j][0]) or []
        if not ins or not outs:
            continue
        rin = p[ins[min(entry_local, len(ins) - 1)]]
        rout = p[outs[-1]]
        atm = atm_of(rin, step)

        # one identical sample across every structure, as in run()
        vals, ok = {}, True
        for kind in kinds:
            legs = _atm_legs(kind, atm)
            vi = leg_value(rin, legs, step)
            vo = leg_value(rout, legs, step)
            if vi is None or vo is None or vi <= 0:
                ok = False
                break
            vals[kind] = (vi, vo)
        if not ok:
            dropped += 1
            continue
        dtes.append((tgt - day).days)
        half = "all" if split is None else ("train" if day < split else "test")
        for kind, (vi, vo) in vals.items():
            debit = vi * (1.0 + cost_pct)
            pnl = vo * (1.0 - cost_pct) - debit
            out.setdefault((kind, 0, half), []).append((pnl, debit))
    if dtes:
        progress("  %d entry days tried, %d measured, %d dropped off the ceiling,"
                 " entry DTE %.0f-%.0f (med %.0f)"
                 % (tried, len(dtes), dropped, min(dtes), max(dtes),
                    sorted(dtes)[len(dtes) // 2]))
    return out


def show(out, kinds=("call", "put"), widths=(2, 4, 5), halves=("all",)):
    for kind in kinds:
        for w in widths:
            for half in halves:
                v = out.get((kind, w, half)) or []
                if len(v) < 5:
                    continue
                pnl = [x for x, _ in v]
                deb = [y for _, y in v]
                n = len(pnl)
                m = sum(pnl) / n
                md = sum(deb) / n
                sd = (sum((x - m) ** 2 for x in pnl) / (n - 1)) ** 0.5
                t = m / (sd / n ** 0.5) if sd else 0.0
                print("  long %-4s %d-wide %-6s n=%-4d debit %6.1f -> EV %+6.2f pts"
                      " (%+6.1f%%)  win %4.1f%%  t %+5.2f"
                      % (kind, w, half, n, md, m, 100.0 * m / md,
                         100.0 * sum(1 for x in pnl if x > 0) / n, t))


def main() -> None:
    symbol = str(arg("--index", "NIFTY")).upper()
    min_dte = int(arg("--min-dte", "20"))
    max_dte = int(arg("--max-dte", "45"))
    holds = [int(h) for h in str(arg("--hold", "1,3,5,10")).split(",")]
    cost = float(arg("--cost", "0.005"))
    for h in holds:
        print("\n=== hold %d session(s), entry %d-%d DTE, cost %.2f%% per side ==="
              % (h, min_dte, max_dte, cost * 100))
        res = run(symbol, min_dte=min_dte, max_dte=max_dte, hold=h, cost_pct=cost)
        show(res)


if __name__ == "__main__":
    main()
