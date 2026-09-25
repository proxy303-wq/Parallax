"""Sigma-normalised condor shapes: is a fixed strike COUNT the right rule?

The live shape is 3-3 -- shorts three strikes from ATM, hedges three strikes
further out.  That was tuned on NIFTY, where one strike is 50 points on a
~23,400 index (0.21% of spot) and IV is low, so the shorts sit ~0.85 of a daily
sigma away.

The same three strikes mean something completely different elsewhere:

    NIFTY     150 pts   0.64% of spot   ~0.85 sigma
    BANKNIFTY 300 pts   0.58% of spot   ~0.61 sigma
    SENSEX    300 pts   0.40% of spot   ~0.39 sigma
    BANKEX    300 pts   0.48% of spot   ~0.30 sigma

So the identical config is a third as safe on the BSE indices.  On 2026-09-24
SENSEX moved -619 points (0.83%) after entry against 300 points of room and the
0DTE book lost Rs 15,088 while BANKEX, which moved only 0.29%, kept Rs 5,220.

This module replaces the strike COUNT with a distance scaled to the market's own
expected move, read straight off the ladder as the ATM straddle price (no IV
model needed), and sweeps the multiple k:

    short_distance = k * ATM_straddle
    wing           = a fixed number of strikes beyond the shorts

Everything is reported per index, on a train/test split, with the same
intrabar treatment and cost model as condor_study.
"""
from __future__ import annotations

import datetime
from collections import OrderedDict

from parallax.apps.research.condor_study import IST, MAX_OFF, _intrabar_extremes, _value
from parallax.config.indices import spec as index_spec

EXPIRY_WEEKDAY = {"NIFTY": 1, "BANKNIFTY": 1, "FINNIFTY": 1, "SENSEX": 3, "BANKEX": 3}


def _is_last_of_month(d: datetime.date, weekday: int) -> bool:
    return d.weekday() == weekday and (d + datetime.timedelta(days=7)).month != d.month


def is_expiry(symbol: str, d: datetime.date) -> bool:
    """NIFTY/SENSEX are weekly; BANKNIFTY/BANKEX are the monthly (last) one."""
    wd = EXPIRY_WEEKDAY.get(symbol)
    if wd is None:
        return False
    if symbol in ("BANKNIFTY", "BANKEX"):
        return _is_last_of_month(d, wd)
    return d.weekday() == wd


def _straddle(row) -> float | None:
    """ATM call + put close: the market's own expected move to expiry."""
    c = (row.get("CALL") or {}).get(0.0)
    p = (row.get("PUT") or {}).get(0.0)
    if not c or not p:
        return None
    return float(c[0]) + float(p[0])


def legs_for(atm, step, short_strikes, wing_strikes):
    return [("sp", atm - short_strikes * step, "PUT", 1),
            ("sc", atm + short_strikes * step, "CALL", 1),
            ("hp", atm - (short_strikes + wing_strikes) * step, "PUT", -1),
            ("hc", atm + (short_strikes + wing_strikes) * step, "CALL", -1)]


def run_shape(idx, byts, k=None, fixed_strikes=None, wing_strikes=3,
              train_end=datetime.date(2026, 2, 1), lots=5, cost_pct=0.0,
              entry_bar=1, date_from=None, date_to=None, require_close=True,
              verbose=False):
    """One shape over every expiry in the ladder.

    k               -> shorts at k * ATM_straddle (sigma-normalised)
    fixed_strikes   -> shorts at a fixed strike count (the current rule)
    Exactly one of the two must be given.  date_from/date_to bound the walk-forward
    window so a single month can be replayed on its own.
    """
    step = float(idx.step)
    days = OrderedDict()
    for ts in sorted(byts):
        d = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).astimezone(IST).date()
        if date_from and d < date_from:
            continue
        if date_to and d > date_to:
            continue
        days.setdefault(d, []).append(ts)

    out, misses, skipped, unresolved = [], 0, 0, 0
    for d in sorted(days):
        if not is_expiry(idx.symbol, d):
            continue
        tss = days.get(d) or []
        if len(tss) < 60:
            continue
        # Bars run 09:15, 09:20, 09:25 ... so entry_bar=1 is 09:20 (what the
        # live runner uses) and entry_bar=3 is 09:30.
        if len(tss) <= entry_bar + 1:
            skipped += 1
            continue
        ed = byts[tss[entry_bar]]
        atm = round(ed["spot"] / step) * step
        strad = _straddle(ed)
        if strad is None or strad <= 0:
            skipped += 1
            continue

        if fixed_strikes is not None:
            ss = int(fixed_strikes)
        else:
            ss = int(round(k * strad / step))
        if ss < 1:
            ss = 1
        if (ss + wing_strikes) > MAX_OFF:
            skipped += 1
            continue

        legs = legs_for(atm, step, ss, wing_strikes)
        credit = _value(ed, legs, atm, step)
        if credit is None:
            misses += 1
            continue
        if credit <= 0:
            skipped += 1
            continue

        # The legs are fixed at the entry atm and re-expressed against each later
        # bar's OWN atm, so once the index moves far enough a far leg's offset
        # leaves the ladder and _value returns None.  Skipping that bar leaves
        # 'last' at whatever the last VALUABLE bar said -- and on a trending day
        # the whole tail of the session can be unvaluable, so the trade gets
        # scored on a stale mark and a 300-point ITM finish is never seen at all.
        # That is not a rounding error: it turned every BANKEX expiry into a
        # full-credit win.  require_close drops those trades instead.
        peak, last, last_j = 0.0, None, None
        for j, ts in enumerate(tss[entry_bar + 1:], start=entry_bar + 1):
            row = byts[ts]
            at = round(row["spot"] / step) * step
            val = _value(row, legs, at, step)
            if val is None:
                misses += 1
                continue
            last = (credit - val) / credit
            last_j = j
            peak = max(peak, last)
        if last is None:
            skipped += 1
            continue
        if require_close and last_j != len(tss) - 1:
            unresolved += 1
            continue

        short_pts = ss * step
        max_loss = short_pts + wing_strikes * step - credit
        out.append({
            "date": d, "atm": atm, "spot": ed["spot"], "straddle": strad,
            "short_strikes": ss, "short_pts": short_pts,
            "sigma_room": short_pts / strad,
            "credit": credit, "credit_pct": 100.0 * credit / ed["spot"],
            "max_loss": max_loss,
            "rr": credit / max_loss if max_loss > 0 else 0.0,
            "room_per_credit": (short_pts / strad) / (credit / ed["spot"]) if credit > 0 else 0.0,
            "pct": last,
            "pnl": (last * credit - cost_pct * credit) * idx.lot * lots,
            "entry": credit * idx.lot * lots,
            "closed": last_j == len(tss) - 1,
            "train": d < train_end,
        })
    return out, misses, skipped, unresolved


def summarise(rows):
    import statistics
    if not rows:
        return None
    n = len(rows)
    wins = [r for r in rows if r["pnl"] > 0]
    gl = -sum(r["pnl"] for r in rows if r["pnl"] < 0)
    gw = sum(r["pnl"] for r in wins)
    tot = sum(r["pnl"] for r in rows)
    eq = pk = dd = 0.0
    for r in rows:
        eq += r["pnl"]; pk = max(pk, eq); dd = max(dd, pk - eq)
    mean = tot / n
    sd = statistics.pstdev([r["pnl"] for r in rows]) if n > 1 else 0.0
    se = sd / (n ** 0.5) if n > 1 else 0.0
    return {
        "n": n, "win": len(wins) / n, "pf": (gw / gl) if gl else float("inf"),
        "total": tot, "maxdd": dd, "t": (mean / se) if se else 0.0,
        "strikes": statistics.median([r["short_strikes"] for r in rows]),
        "sigma_room": statistics.median([r["sigma_room"] for r in rows]),
        "credit_pct": statistics.median([r["credit_pct"] for r in rows]),
        "rr": statistics.median([r["rr"] for r in rows]),
        "room_per_credit": statistics.median([r["room_per_credit"] for r in rows]),
    }
