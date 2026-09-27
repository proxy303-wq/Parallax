"""How far will the market move TODAY?  Read it off the ATM straddle.

Calibrated on the +-10 ladder, 2025-01-05 .. 2026-09-01, at the 09:20 bar, to
that session's close.  Every figure is a multiple of the ATM straddle read at
09:20, which is the market's own expected move and needs no vol model.

The tables are keyed by DAYS TO EXPIRY, because that is what decides how much of
the straddle belongs to today.  Reading an expiry-day multiplier against a
ten-day straddle understates the move by about 2x -- that mistake is what this
structure exists to prevent.

                       N I F T Y                          S E N S E X
  dte   n    net move x straddle (p10..p95)   n    net move x straddle (p10..p95)
   0   83    0.11 0.19 0.64 0.91 1.50 1.94   79    0.05 0.20 0.52 0.99 1.42 2.10
   1   84    0.11 0.22 0.43 0.64 1.07 1.34   82    0.05 0.14 0.37 0.62 0.96 1.12
   2    1    (too few - interpolated)         83    0.09 0.21 0.48 0.79 1.36 1.67
  3-4  81    0.07 0.21 0.39 0.60 0.79 0.90   85    0.05 0.17 0.38 0.57 0.90 0.99
  5+  161    0.04 0.14 0.32 0.61 1.00 1.30   81    0.07 0.18 0.34 0.56 0.77 0.87

The multiplier decays roughly as 1/sqrt(T): NIFTY 0.64 on expiry day, 0.43 one
day out, 0.32 at five or more.  A single session is simply a smaller share of a
longer straddle.

Two things worth knowing.  The market ROAMS much further than it ENDS UP -- on
expiry day the NIFTY range runs 1.15x the straddle while the net move is only
0.64x, and a condor is settled by the second number.  And the RELATIVE tail is
fattest on expiry day (p95 = 1.94x against 0.90x at three to four days), which
is the high-gamma session behaving exactly as it should.

BANKNIFTY and BANKEX are deliberately NOT calibrated: monthly expiries give too
few observations, and BANKEX serves bad prints at the strikes we trade (a 62,800
put quoted at 55,003).
"""
from __future__ import annotations

import datetime

PS = (10, 25, 50, 75, 90, 95)

#: symbol -> days-to-expiry bucket -> {"move": {p: x}, "range": {p: x}, "n": int}
CAL = {
    "NIFTY": {
        "0":   {"n": 83,  "move": {10: 0.11, 25: 0.19, 50: 0.64, 75: 0.91, 90: 1.50, 95: 1.94},
                            "range": {10: 0.50, 25: 0.75, 50: 1.15, 75: 1.72, 90: 2.10, 95: 2.54}},
        "1":   {"n": 84,  "move": {10: 0.11, 25: 0.22, 50: 0.43, 75: 0.64, 90: 1.07, 95: 1.34},
                            "range": {10: 0.40, 25: 0.58, 50: 0.78, 75: 1.10, 90: 1.45, 95: 1.75}},
        "3-4": {"n": 81,  "move": {10: 0.07, 25: 0.21, 50: 0.39, 75: 0.60, 90: 0.79, 95: 0.90},
                            "range": {10: 0.33, 25: 0.47, 50: 0.65, 75: 0.91, 90: 1.12, 95: 1.30}},
        "5+":  {"n": 161, "move": {10: 0.04, 25: 0.14, 50: 0.32, 75: 0.61, 90: 1.00, 95: 1.30},
                            "range": {10: 0.35, 25: 0.50, 50: 0.70, 75: 0.98, 90: 1.30, 95: 1.55}},
    },
    "SENSEX": {
        "0":   {"n": 79,  "move": {10: 0.05, 25: 0.20, 50: 0.52, 75: 0.99, 90: 1.42, 95: 2.10},
                            "range": {10: 0.31, 25: 0.61, 50: 1.22, 75: 1.76, 90: 2.30, 95: 2.46}},
        "1":   {"n": 82,  "move": {10: 0.05, 25: 0.14, 50: 0.37, 75: 0.62, 90: 0.96, 95: 1.12},
                            "range": {10: 0.32, 25: 0.48, 50: 0.77, 75: 1.11, 90: 1.46, 95: 1.70}},
        "2":   {"n": 83,  "move": {10: 0.09, 25: 0.21, 50: 0.48, 75: 0.79, 90: 1.36, 95: 1.67},
                            "range": {10: 0.38, 25: 0.55, 50: 0.92, 75: 1.35, 90: 1.85, 95: 2.20}},
        "3-4": {"n": 85,  "move": {10: 0.05, 25: 0.17, 50: 0.38, 75: 0.57, 90: 0.90, 95: 0.99},
                            "range": {10: 0.30, 25: 0.45, 50: 0.73, 75: 1.02, 90: 1.30, 95: 1.50}},
        "5+":  {"n": 81,  "move": {10: 0.07, 25: 0.18, 50: 0.34, 75: 0.56, 90: 0.77, 95: 0.87},
                            "range": {10: 0.24, 25: 0.36, 50: 0.59, 75: 0.83, 90: 1.05, 95: 1.20}},
    },
}


def bucket_for(dte: int) -> str:
    d = int(dte)
    if d <= 0:
        return "0"
    if d == 1:
        return "1"
    if d == 2:
        return "2"
    if d <= 4:
        return "3-4"
    return "5+"


def days_to_expiry(expiry: str, today: datetime.date | None = None) -> int:
    e = datetime.date.fromisoformat(str(expiry)[:10])
    t = today or datetime.date.today()
    return (e - t).days


def _blend(a: dict, b: dict) -> dict:
    return {p: (a[p] + b[p]) / 2.0 for p in PS}


def calibrate(symbol: str, dte: int):
    """(move_table, range_table, bucket, n) - interpolating a thin bucket."""
    s = str(symbol).upper()
    if s not in CAL:
        return None, None, None, 0
    table = CAL[s]
    b = bucket_for(dte)
    if b in table and table[b]["n"] >= 20:
        return table[b]["move"], table[b]["range"], b, table[b]["n"]
    # a bucket too thin to trust (NIFTY at 2 days out): blend its neighbours
    order = ["0", "1", "2", "3-4", "5+"]
    i = order.index(b)
    lo = next((table[k] for k in reversed(order[:i]) if k in table), None)
    hi = next((table[k] for k in order[i + 1:] if k in table), None)
    if lo and hi:
        return _blend(lo["move"], hi["move"]), _blend(lo["range"], hi["range"]), b + "*", 0
    src = lo or hi
    if not src:
        return None, None, None, 0
    return src["move"], src["range"], b + "*", src["n"]


def _interp(table: dict, x: float) -> float:
    xs = [table[p] for p in PS]
    if x <= xs[0]:
        return PS[0] / 100.0
    if x >= xs[-1]:
        return PS[-1] / 100.0
    for i in range(1, len(xs)):
        if x <= xs[i]:
            f = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
            return (PS[i - 1] + f * (PS[i] - PS[i - 1])) / 100.0
    return PS[-1] / 100.0


def forecast(symbol: str, expiry: str | None = None) -> dict | None:
    """Live range forecast, using the table for the contract's days-to-expiry."""
    from parallax.adapters.market_data.dhan_options import atm_straddle
    from parallax.config.indices import spec as _spec
    d = atm_straddle(symbol, expiry)
    if not d:
        return None
    step = float(_spec(symbol).step)
    dte = days_to_expiry(d["expiry"]) if d.get("expiry") else 0
    move_x, range_x, buck, n = calibrate(symbol, dte)
    S = d["straddle"]
    out = dict(d)
    out.update({"step": step, "dte": dte, "bucket": buck, "samples": n,
                "calibrated": move_x is not None})
    if not move_x:
        return out
    out["proj_net_move"] = move_x[50] * S
    out["proj_range"] = range_x[50] * S
    out["move_bands"] = {p: move_x[p] * S for p in PS}
    out["range_bands"] = {p: range_x[p] * S for p in PS}
    return out


def prob_move_within(symbol: str, pts: float, straddle: float, dte: int) -> float | None:
    move_x, _, _, _ = calibrate(symbol, dte)
    if not move_x or straddle <= 0:
        return None
    return _interp(move_x, float(pts) / float(straddle))


def prob_profit(symbol: str, short_pts: float, credit_pts: float,
                straddle: float, dte: int) -> float | None:
    """P(the condor finishes in profit).

    Uses the BREAK-EVEN (short + credit), not the short distance.  A condor is
    paid out to its break-even, and using short_pts alone under-predicts the win
    rate by the credit: SENSEX 5/-3 reads 0.65 that way against 0.74 measured.
    """
    return prob_move_within(symbol, float(short_pts) + float(credit_pts), straddle, dte)


def strikes_for_profit(symbol: str, straddle: float, credit_pts: float,
                       target: float, dte: int) -> int | None:
    from parallax.config.indices import spec as _spec
    move_x, _, _, _ = calibrate(symbol, dte)
    if not move_x or straddle <= 0:
        return None
    step = float(_spec(symbol).step)
    for so in range(1, 21):
        if _interp(move_x, (so * step + float(credit_pts)) / straddle) >= target:
            return so
    return None
