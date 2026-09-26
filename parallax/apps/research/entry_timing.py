"""0DTE vs 1DTE entry timing for the index condor.

Every study so far - and the live workers - enter ON the expiry day at 09:20,
which gives the position about six hours of life.  Entering one session earlier
puts the same shape on the book a day before expiry: two sessions of decay
against the same wing width, but with the strikes fixed a day early, so an
overnight gap can open with the shorts already ITM.

run_shape() cannot answer this.  It skips any day that is not an expiry, so by
construction it only ever sees the 0DTE case.  This module walks the entry day
back through the ladder's own trading calendar instead, then values the position
along the SAME bar sequence: every bar from the one after entry through the
expiry close.

The ladder is a rolling ATM-relative series, so a strike fixed at the entry
bar's ATM is re-expressed against each later bar's own ATM.  Once the index
moves far enough a far leg leaves the ladder and _value returns None; those bars
are skipped, and require_close drops any trade whose final bar could not be
valued (the stale-mark bug that once made every BANKEX expiry look like a win).
"""
from __future__ import annotations

import datetime
from collections import OrderedDict

from parallax.apps.research.condor_study import IST, MAX_OFF, _value
from parallax.apps.research.sigma_shape import _straddle, is_expiry, legs_for


def _days(byts):
    days = OrderedDict()
    for ts in sorted(byts):
        d = (datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
             .astimezone(IST).date())
        days.setdefault(d, []).append(ts)
    return days


def run_timing(idx, byts, fixed_strikes, wing_strikes=3, entry_days_before=0,
               lots=5, cost_pct=0.0, entry_bar=1, require_close=True,
               date_from=None, date_to=None):
    """One shape, one entry convention, over every expiry in the ladder.

    entry_days_before=0 -> enter on the expiry day (0DTE, what runs live)
    entry_days_before=1 -> enter the previous trading day (1DTE)
    """
    step = float(idx.step)
    days = _days(byts)
    dates = sorted(days)
    prev_of = {dates[i]: dates[i - 1] for i in range(1, len(dates))}

    out, misses, skipped, unresolved = [], 0, 0, 0
    for expiry_day in dates:
        if not is_expiry(idx.symbol, expiry_day):
            continue
        if date_from and expiry_day < date_from:
            continue
        if date_to and expiry_day > date_to:
            continue
        if (fixed_strikes + wing_strikes) > MAX_OFF:
            skipped += 1
            continue

        entry_day, ok = expiry_day, True
        for _ in range(entry_days_before):
            entry_day = prev_of.get(entry_day)
            if entry_day is None:
                ok = False
                break
        if not ok:
            skipped += 1
            continue

        ets = days.get(entry_day) or []
        if len(ets) <= entry_bar + 1:
            skipped += 1
            continue
        ed = byts[ets[entry_bar]]
        atm = round(ed["spot"] / step) * step
        strad = _straddle(ed)
        if strad is None or strad <= 0:
            skipped += 1
            continue
        legs = legs_for(atm, step, fixed_strikes, wing_strikes)
        credit = _value(ed, legs, atm, step)
        if credit is None:
            misses += 1
            continue
        if credit <= 0:
            skipped += 1
            continue

        seq = list(ets[entry_bar + 1:])
        for d2 in dates:
            if entry_day < d2 <= expiry_day:
                seq.extend(days[d2])
        if not seq:
            skipped += 1
            continue

        peak, last, last_j, unvalued = 0.0, None, None, 0
        for j, ts in enumerate(seq):
            row = byts[ts]
            at = round(row["spot"] / step) * step
            val = _value(row, legs, at, step)
            if val is None:
                misses += 1
                unvalued += 1
                continue
            last = (credit - val) / credit
            last_j = j
            peak = max(peak, last)
        if last is None:
            skipped += 1
            continue
        if require_close and last_j != len(seq) - 1:
            unresolved += 1
            continue

        short_pts = fixed_strikes * step
        max_loss = wing_strikes * step - credit
        out.append({
            "expiry": expiry_day, "entry_day": entry_day,
            "atm": atm, "spot": ed["spot"], "short_strikes": fixed_strikes,
            "short_pts": short_pts, "credit": credit,
            "credit_pct": 100.0 * credit / ed["spot"],
            "max_loss": max_loss,
            "rr": credit / max_loss if max_loss > 0 else 0.0,
            "sigma_room": short_pts / strad,
            "room_per_credit": ((short_pts / strad) / (credit / ed["spot"])
                                if credit > 0 else 0.0),
            "pct": last, "peak": peak,
            "pnl": (last * credit - cost_pct * credit) * idx.lot * lots,
            "entry": credit * idx.lot * lots,
            "bars": len(seq), "unvalued": unvalued,
            "closed": last_j == len(seq) - 1,
        })
    return out, misses, skipped, unresolved
