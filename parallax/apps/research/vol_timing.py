"""Can buying premium be made to pay WITHOUT predicting direction?

The directional version failed on the signal: the plan's 15m regime + 5m
breakout moved NIFTY no more than a random bar did, so a debit spread on it
started from a zero edge and paid a debit on top.

This tests the other source of edge available to a buyer.  A long option is a
bet that REALISED volatility exceeds IMPLIED.  Direction is irrelevant to that
bet; the only thing that matters is whether you paid less for the move than the
move turned out to be worth.  So the entry condition here is not "the index is
going up", it is "the straddle is cheap relative to what the index has actually
been doing lately".

Two structures are priced off the real chain ladder, entered at 09:20 and exited
at the 15:25 close:

  straddle  ATM call + ATM put - the purest long-gamma expression
  strangle  the +-2 strike wings - the same bet bought cheaper, needing a
            bigger move to pay

Both are reconstructed at their ENTRY strikes, not at each bar's own ATM: the
ladder is an ATM-relative rolling series, so a fixed strike drifts to a
different offset as the index moves, and reading it at offset 0 at the exit bar
would silently turn the position into a brand new ATM straddle every time.

Usage:
    python -m parallax.apps.research.vol_timing --index NIFTY
"""
from __future__ import annotations

import datetime
import math
import os
import pickle
import sys

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
HERE = os.path.dirname(os.path.abspath(__file__))

START = "2025-01-05"
END = "2026-09-01"
#: trailing sessions used for the realised estimate
TRAIL_N = 10
#: buy at the ask / sell at the bid, as a fraction of the premium per side
COST_PCT = 0.005


def arg(flag, default):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def load_ladder(symbol, start=START, end=END, progress=print):
    """Prefer the deeper +-10 cache; fall back to the +-8 one."""
    for pat in ("_ladder_%s_%s_%s_off10.pkl", "_ladder_%s_%s_%s.pkl"):
        p = os.path.join(HERE, pat % (symbol, start, end))
        if os.path.exists(p):
            with open(p, "rb") as fh:
                d = pickle.load(fh)
            depth = max(abs(float(k)) for k in next(iter(d.values()))["CALL"])
            progress("  %s: %d bars, depth +-%.0f" % (os.path.basename(p), len(d), depth))
            return d
    raise SystemExit("no ladder cache for %s %s..%s" % (symbol, start, end))


def _ist(ts):
    return datetime.datetime.fromtimestamp(int(ts), IST)


def next_expiry(symbol, d):
    from parallax.apps.research.sigma_shape import EXPIRY_WEEKDAY
    wd = EXPIRY_WEEKDAY[symbol]
    for k in range(8):
        e = d + datetime.timedelta(days=k)
        if e.weekday() == wd:
            return e
    return None


def sessions(ladder):
    """[(date, [ts ascending])] - one entry per trading session."""
    out = []
    cur_day, cur_ts = None, []
    for ts in sorted(ladder):
        day = _ist(ts).date()
        if day != cur_day:
            if cur_ts:
                out.append((cur_day, cur_ts))
            cur_day, cur_ts = day, []
        cur_ts.append(ts)
    if cur_ts:
        out.append((cur_day, cur_ts))
    return out


def legs_at(row, strikes, step):
    """Value of FIXED strikes, re-expressed against this bar's own ATM.

    The ladder stores every option at an offset from the CURRENT atm, so a fixed
    strike drifts to a different key as the index moves.  The first version of
    this function took offsets and rebuilt the key from the new atm, which
    reduces to reading the same offset - i.e. it re-struck the position to the
    new ATM at every bar.  That is not a fixed position at all, it is a rolling
    ATM straddle, and on a decaying instrument it produced a beautiful and
    entirely fake t of -21.
    """
    atm = round(row["spot"] / step) * step
    tot = 0.0
    for strike, ot in strikes:
        key = round((strike - atm) / step) * step
        t = (row.get(ot) or {}).get(key)
        if t is None:
            return None
        tot += float(t[0])
    return tot


def measure(ladder, symbol="NIFTY", entry_local=1, wing_strikes=2,
            cost_pct=COST_PCT, progress=print):
    """One row per session: entry premium, exit value, and the realised move."""
    from parallax.config.indices import spec
    step = float(spec(symbol).step)
    sess = sessions(ladder)
    rows, skipped = [], 0
    prior_moves = []

    for day, tss in sess:
        if len(tss) < 20:
            skipped += 1
            continue
        i0 = min(entry_local, len(tss) - 1)
        t_in, t_out = tss[i0], tss[-1]
        rin, rout = ladder[t_in], ladder[t_out]
        atm_in = round(rin["spot"] / step) * step
        move = float(rout["spot"]) - float(rin["spot"])

        # trailing realised: mean absolute net move of the PREVIOUS sessions
        trail = (sum(prior_moves[-TRAIL_N:]) / len(prior_moves[-TRAIL_N:])
                 if len(prior_moves) >= 5 else None)
        prior_moves.append(abs(move))

        exp = next_expiry(symbol, day)
        dte = (exp - day).days if exp else None

        out = {"day": day, "dte": dte, "move": move, "trail": trail,
               "atm_in": atm_in}
        for tag, strikes in (
                ("strad", [(atm_in, "CALL"), (atm_in, "PUT")]),
                ("strang", [(atm_in + wing_strikes * step, "CALL"),
                            (atm_in - wing_strikes * step, "PUT")])):
            v_in = legs_at(rin, strikes, step)
            v_out = legs_at(rout, strikes, step)
            if v_in is None or v_out is None:
                out[tag] = None
                continue
            entry = v_in * (1.0 + cost_pct)
            exitv = v_out * (1.0 - cost_pct)
            out[tag] = exitv - entry
            out[tag + "_in"] = v_in
        rows.append(out)

    progress("  %d sessions measured, %d skipped" % (len(rows), skipped))
    return rows


def legs_at_signed(row, legs, step):
    """Net value of a signed leg set at FIXED strikes.  See legs_at."""
    atm = round(row["spot"] / step) * step
    tot = 0.0
    for strike, ot, sgn in legs:
        key = round((strike - atm) / step) * step
        t = (row.get(ot) or {}).get(key)
        if t is None:
            return None
        tot += sgn * float(t[0])
    return tot


def holding_curve(ladder, symbol="NIFTY", entry_local=1, cost_pct=COST_PCT,
                  progress=print):
    """Straddle P&L by how long it is held.  Theta is a clock, so the question
    is whether ANY intraday horizon gets the buyer out ahead."""
    from parallax.config.indices import spec
    step = float(spec(symbol).step)
    sess = sessions(ladder)
    offs = (3, 6, 12, 24, 48, 999)
    acc = {o: [] for o in offs}
    for day, tss in sess:
        if len(tss) < 20:
            continue
        i0 = min(entry_local, len(tss) - 1)
        rin = ladder[tss[i0]]
        atm = round(rin["spot"] / step) * step
        legs = [(atm, "CALL"), (atm, "PUT")]
        v_in = legs_at(rin, legs, step)
        if v_in is None:
            continue
        entry = v_in * (1.0 + cost_pct)
        for o in offs:
            k = min(i0 + o, len(tss) - 1)
            v = legs_at(ladder[tss[k]], legs, step)
            if v is not None:
                acc[o].append(v * (1.0 - cost_pct) - entry)
    progress("")
    return acc


def spread_ev(ladder, symbol="NIFTY", entry_local=1, cost_pct=COST_PCT,
              progress=print):
    """The plan's own structure: a debit spread, priced off the real chain.

    A debit spread is long one option and SHORT another, so it collects part of
    the variance risk premium back on the short leg.  That should make it lose
    less than an outright - the question is whether it loses less than nothing.
    """
    from parallax.config.indices import spec
    step = float(spec(symbol).step)
    sess = sessions(ladder)
    widths = (2, 4, 5)
    out = {}
    for day, tss in sess:
        if len(tss) < 20:
            continue
        i0 = min(entry_local, len(tss) - 1)
        rin, rout = ladder[tss[i0]], ladder[tss[-1]]
        atm = round(rin["spot"] / step) * step
        for w in widths:
            for kind, ot in (("call", "CALL"), ("put", "PUT")):
                sgn = 1.0 if kind == "call" else -1.0
                far = atm + sgn * w * step
                legs = [(atm, ot, 1.0), (far, ot, -1.0)]
                v_in = legs_at_signed(rin, legs, step)
                v_out = legs_at_signed(rout, legs, step)
                if v_in is None or v_out is None or v_in <= 0:
                    continue
                debit = v_in * (1.0 + cost_pct)
                out.setdefault((kind, w), []).append(
                    (v_out * (1.0 - cost_pct) - debit, debit))
    progress("")
    return out


def _stat(vals):
    n = len(vals)
    if n < 2:
        return None
    m = sum(vals) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in vals) / (n - 1))
    return m, sd, m / (sd / math.sqrt(n)) if sd > 0 else 0.0, n


def show(rows, tag, label):
    vals = [r[tag] for r in rows if r.get(tag) is not None]
    s = _stat(vals)
    if not s:
        print("  %-26s (none)" % label)
        return
    m, sd, t, n = s
    win = 100.0 * sum(1 for v in vals if v > 0) / n
    print("  %-26s n=%-4d mean %+8.1f  sd %7.1f  win %4.1f%%  t %+5.2f"
          % (label, n, m, sd, win, t))


def main() -> None:
    symbol = str(arg("--index", "NIFTY")).upper()
    print("loading %s ladder" % symbol, flush=True)
    lad = load_ladder(symbol)
    rows = measure(lad, symbol)

    usable = [r for r in rows if r.get("strad") is not None and r.get("trail")]
    print("\nusable sessions: %d\n" % len(usable))

    print("=== long premium, entered 09:20, exited at the close ===")
    show(usable, "strad", "all sessions - straddle")
    show(usable, "strang", "all sessions - strangle")

    # dte matters enormously: a 0DTE straddle and a 5-day straddle are
    # different instruments, and mixing them hides both.
    print("\n=== by days to expiry ===")
    for lo, hi, name in ((0, 0, "0 dte (expiry day)"), (1, 1, "1 dte"),
                         (2, 2, "2 dte"), (3, 4, "3-4 dte"), (5, 9, "5+ dte")):
        sub = [r for r in usable if r["dte"] is not None and lo <= r["dte"] <= hi]
        if sub:
            show(sub, "strad", name)
    print("  (strangle)")
    for lo, hi, name in ((0, 0, "0 dte (expiry day)"), (1, 1, "1 dte"),
                         (2, 2, "2 dte"), (3, 4, "3-4 dte"), (5, 9, "5+ dte")):
        sub = [r for r in usable if r["dte"] is not None and lo <= r["dte"] <= hi]
        if sub:
            show(sub, "strang", name)

    # THE central question: does cheapness predict anything?
    print("\n=== conditional on the straddle being cheap vs trailing realised ===")
    print("(cheapness = straddle / trailing mean |daily move|; terciles within dte)")
    for lo, hi, name in ((0, 0, "0 dte"), (1, 4, "1-4 dte"), (5, 9, "5+ dte")):
        sub = [r for r in usable if r["dte"] is not None and lo <= r["dte"] <= hi]
        if len(sub) < 12:
            continue
        for r in sub:
            r["_ratio"] = r["strad_in"] / r["trail"] if r["trail"] else 0.0
        sub.sort(key=lambda r: r["_ratio"])
        k = len(sub) // 3
        for lab, part in (("cheap", sub[:k]), ("mid", sub[k:2 * k]),
                          ("rich", sub[2 * k:])):
            show(part, "strad", "%s / %s" % (name, lab))

    print("\n=== holding curve: does ANY intraday horizon pay? ===")
    print("(mean straddle P&L in points, exited this many 5-min bars after entry)")
    curve = holding_curve(lad, symbol)
    for o in (3, 6, 12, 24, 48, 999):
        v = curve.get(o) or []
        if not v:
            continue
        s = _stat(v)
        label = "to the close" if o == 999 else "+%d min" % (o * 5)
        print("  %-14s n=%-4d mean %+7.1f  sd %6.1f  win %4.1f%%  t %+5.2f"
              % (label, s[3], s[0], s[1],
                 100.0 * sum(1 for x in v if x > 0) / s[3], s[2]))

    print("\n=== the plan's own structure: debit spreads, priced off the chain ===")
    print("(long ATM, short W strikes out; entered 09:20, exited at the close)")
    sp = spread_ev(lad, symbol)
    for kind in ("call", "put"):
        for w in (2, 4, 5):
            v = sp.get((kind, w)) or []
            if not v:
                continue
            pnl = [p for p, _ in v]
            deb = [d for _, d in v]
            s = _stat(pnl)
            md = sum(deb) / len(deb)
            print("  long %-4s spread %d strikes wide  n=%-4d mean debit %6.1f "
                  "-> EV %+6.1f pts (%+6.1f%% of debit)  win %4.1f%%  t %+5.2f"
                  % (kind, w, len(pnl), md, s[0], 100.0 * s[0] / md,
                     100.0 * sum(1 for p in pnl if p > 0) / len(pnl), s[2]))

    print("\n=== did the move beat what was paid for it? ===")
    for lo, hi, name in ((0, 0, "0 dte"), (1, 4, "1-4 dte"), (5, 9, "5+ dte")):
        sub = [r for r in usable if r["dte"] is not None and lo <= r["dte"] <= hi]
        if not sub:
            continue
        beat = 100.0 * sum(1 for r in sub if abs(r["move"]) > r["strad_in"]) / len(sub)
        print("  %-12s n=%-4d  |move| > entry straddle: %4.1f%%"
              % (name, len(sub), beat))


if __name__ == "__main__":
    main()
