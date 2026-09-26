"""Diagonal calendar spreads - Katwal, "Definitive Guide to Advanced Options
Trading", 6.3.1/6.3.2.  He calls them his all-time favourites and runs them with
heavy volume.

The structure (bearish/bullish flavour is just which side you sell):

    SELL a near-strike option of the CURRENT expiry
    BUY  a further-strike option of the NEXT expiry, at about the SAME premium

Why it is a different animal from the condor this repo sells.  A condor's loss
is CAPPED at the wing: once the index passes it, nothing more happens.  A
calendar's short leg decays to zero in the near week while the long leg, a week
further out, still carries time value - so a move AGAINST the position can be
profitable on both legs at once.  Quoting him:

    "if Nifty goes against me by let us say 100 points, 9100 current week put
     will eventually go to 0 giving us profit of 52 points but the bought option
     of 8700 at 49 might go to 70 giving us extra 21 points of profit because
     its expiry is next week and it will retain time value."

That is the property worth testing: the condor is a bet that nothing happens,
the calendar is closer to a bet that the near week decays faster than the far
one.  He is also explicit that the tail is UNDEFINED - "it is not possible to
know what the maximum loss and profit for the strategy can be, because future
value of next week option cannot be determined" - so this measures it rather
than assuming it is bounded.

Both series are ATM-relative from the same underlying spot, so a given offset is
the same strike in either one.
"""
from __future__ import annotations

import datetime
from collections import OrderedDict

from parallax.apps.research.condor_study import IST


def _price(row, strike, otype, atm_t, step, max_off):
    off = strike - atm_t
    if abs(off) > max_off * step + 1e-9:
        return None
    t = row.get(otype, {}).get(off)
    return t[0] if t else None


def _intrinsic(strike, spot, otype):
    return max(strike - spot, 0.0) if otype == "PUT" else max(spot - strike, 0.0)


def run_calendar(idx, near, nxt, side="PUT", short_off=2, entry_days_before=2,
                 entry_bar=1, lots=7, max_off=10, date_from=None, date_to=None):
    """One calendar over every expiry in the ladder.

    side="PUT"  -> bullish put calendar (6.3.1): sell near-expiry put, buy
                   next-expiry put at a further strike.
    side="CALL" -> bearish call calendar (6.3.2).
    short_off   -> strikes out from the ATM for the SOLD (near) leg.
    """
    step = float(idx.step)
    days = OrderedDict()
    for ts in sorted(set(near) & set(nxt)):
        d = (datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
             .astimezone(IST).date())
        days.setdefault(d, []).append(ts)
    dates = sorted(days)
    prev_of = {dates[i]: dates[i - 1] for i in range(1, len(dates))}

    from parallax.apps.research.sigma_shape import is_expiry
    out, skipped, unmatched = [], 0, 0
    for exp_day in dates:
        if not is_expiry(idx.symbol, exp_day):
            continue
        if date_from and exp_day < date_from:
            continue
        if date_to and exp_day > date_to:
            continue
        entry_day, ok = exp_day, True
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
        ed_n, ed_x = near[ets[entry_bar]], nxt[ets[entry_bar]]
        atm = round(ed_n["spot"] / step) * step
        k_short = atm - short_off * step if side == "PUT" else atm + short_off * step
        p_short = _price(ed_n, k_short, side, atm, step, max_off)
        if p_short is None:
            skipped += 1
            continue

        # Katwal's rule: the bought leg is the further strike of the NEXT expiry
        # "bearing approximately same premium" as the sold one.
        best, best_d = None, None
        for b in range(short_off + 1, max_off + 1):
            k = atm - b * step if side == "PUT" else atm + b * step
            p = _price(ed_x, k, side, atm, step, max_off)
            if p is None:
                continue
            d_ = abs(p - p_short)
            if best_d is None or d_ < best_d:
                best, best_d = (k, p, b), d_
        if best is None:
            unmatched += 1
            continue
        k_long, p_long, b_strikes = best
        credit = p_short - p_long

        # walk to the expiry close: the short settles, the long is sold live
        seq = list(ets[entry_bar + 1:])
        for d2 in dates:
            if entry_day < d2 <= exp_day:
                seq.extend(days[d2])
        if not seq:
            skipped += 1
            continue
        last_pnl, last_j = None, None
        for j, ts in enumerate(seq):
            rn, rx = near[ts], nxt[ts]
            at = round(rn["spot"] / step) * step
            spot = rn["spot"]
            p_long_now = _price(rx, k_long, side, at, step, max_off)
            if p_long_now is None:
                continue
            # before expiry the short is still live and marked to market; at the
            # final bar it has settled at intrinsic
            final = (j == len(seq) - 1)
            if final:
                short_val = _intrinsic(k_short, spot, side)
            else:
                short_val = _price(rn, k_short, side, at, step, max_off)
                if short_val is None:
                    continue
            last_pnl = credit - short_val + p_long_now
            last_j = j
        if last_pnl is None:
            skipped += 1
            continue
        if last_j != len(seq) - 1:
            skipped += 1
            continue
        out.append({
            "expiry": exp_day, "entry_day": entry_day, "atm": atm,
            "short_strike": k_short, "long_strike": k_long,
            "short_off": short_off, "long_off": b_strikes,
            "credit": credit, "premium_gap": best_d,
            "pnl": last_pnl * idx.lot * lots,
            "credit_rupees": credit * idx.lot * lots,
        })
    return out, skipped, unmatched
