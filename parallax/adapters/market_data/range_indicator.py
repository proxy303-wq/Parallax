"""How far will the market move TODAY?  Read it off the ATM straddle.

Calibrated on the +-10 ladder, 2025-01-05 .. 2026-09-01, at the 09:20 bar, to
that session's close.  Every figure is a multiple of the ATM straddle read at
09:20 -- the market's own expected move, no vol model involved.

Keyed by DAYS TO EXPIRY, because that decides how much of the straddle belongs
to today.  Reading an expiry-day multiplier against a ten-day straddle understates
the move about twofold.

THE TAIL IS EMPIRICAL, NOT FITTED.  A lognormal fitted to these samples
overstates p90 by 33% and p95 by 59% (NIFTY expiry day: 1.99 against an actual
1.50 at p90).  The real distribution has a THINNER tail than lognormal, so
extrapolating parametrically would inflate every risk number.  Percentiles out
to p99 are measured directly instead -- noisy at the extreme, but honest.

                       N I F T Y                          S E N S E X
  dte   n    net move x straddle                     n    net move x straddle
   0   83    0.10 0.19 0.64 0.87 1.28 1.50 ... 2.50  79   0.05 0.20 0.52 0.96 1.32 1.42 ... 2.82
   1   84    0.10 0.22 0.43 0.63 0.83 1.07 ... 1.58  82   0.05 0.14 0.37 0.62 0.79 0.96 ... 1.84
   2    1    (too few - blends 1 and 3-4)            83   0.09 0.21 0.48 0.76 1.24 1.36 ... 2.45
  3-4  81    0.07 0.20 0.37 0.60 0.72 0.79 ... 1.04  85   0.05 0.16 0.37 0.57 0.70 0.89 ... 1.55
  5+  161    0.04 0.14 0.32 0.61 0.86 1.00 ... 2.80  81   0.07 0.17 0.34 0.56 0.64 0.77 ... 0.99

The multiplier decays roughly as 1/sqrt(T): NIFTY 0.64 on expiry day, 0.43 one
day out, 0.32 at five or more.

Two things worth knowing.  The market ROAMS much further than it ENDS UP -- on
expiry day the NIFTY range is 1.15x the straddle while the net move is only
0.64x, and a condor is settled by the second number.  And the relative tail is
fattest on expiry day (p95 1.94x against 0.90x at three to four days), the
high-gamma session behaving as it should.

BANKNIFTY and BANKEX are deliberately NOT calibrated: monthly expiries give too
few observations, and BANKEX serves bad prints at the strikes we trade (a 62,800
put quoted at 55,003).
"""
from __future__ import annotations

import datetime

PS = (10, 25, 50, 75, 85, 90, 93, 95, 97, 98, 99)

#: symbol -> days-to-expiry bucket -> {"n": int, "move": {p: x}, "range": {p: x}}
CAL = {
    "NIFTY": {
        "0": {"n": 83,
              "move": {10: 0.10, 25: 0.19, 50: 0.64, 75: 0.87, 85: 1.28, 90: 1.50,
                       93: 1.72, 95: 1.94, 97: 2.02, 98: 2.02, 99: 2.50},
              "range": {10: 0.49, 25: 0.75, 50: 1.15, 75: 1.71, 85: 1.89, 90: 2.10,
                        93: 2.26, 95: 2.54, 97: 2.98, 98: 2.98, 99: 3.27}},
        "1": {"n": 84,
              "move": {10: 0.10, 25: 0.22, 50: 0.43, 75: 0.63, 85: 0.83, 90: 1.07,
                       93: 1.15, 95: 1.34, 97: 1.43, 98: 1.47, 99: 1.58},
              "range": {10: 0.43, 25: 0.56, 50: 0.77, 75: 1.10, 85: 1.24, 90: 1.38,
                        93: 1.67, 95: 1.69, 97: 1.71, 98: 1.86, 99: 1.88}},
        "3-4": {"n": 81,
              "move": {10: 0.07, 25: 0.20, 50: 0.37, 75: 0.60, 85: 0.72, 90: 0.79,
                       93: 0.84, 95: 0.90, 97: 1.01, 98: 1.01, 99: 1.04},
              "range": {10: 0.34, 25: 0.44, 50: 0.63, 75: 0.85, 85: 0.97, 90: 1.07,
                        93: 1.15, 95: 1.26, 97: 1.33, 98: 1.33, 99: 1.52}},
        "5+": {"n": 161,
              "move": {10: 0.04, 25: 0.14, 50: 0.32, 75: 0.61, 85: 0.86, 90: 1.00,
                       93: 1.26, 95: 1.30, 97: 1.88, 98: 2.79, 99: 2.80},
              "range": {10: 0.35, 25: 0.50, 50: 0.70, 75: 0.97, 85: 1.31, 90: 1.62,
                        93: 1.95, 95: 2.37, 97: 2.42, 98: 3.14, 99: 3.91}},
    },
    "SENSEX": {
        "0": {"n": 79,
              "move": {10: 0.05, 25: 0.20, 50: 0.52, 75: 0.96, 85: 1.32, 90: 1.42,
                       93: 1.61, 95: 1.80, 97: 2.60, 98: 2.60, 99: 2.82},
              "range": {10: 0.31, 25: 0.61, 50: 1.22, 75: 1.71, 85: 2.04, 90: 2.22,
                        93: 2.33, 95: 2.37, 97: 3.20, 98: 3.20, 99: 3.29}},
        "1": {"n": 82,
              "move": {10: 0.05, 25: 0.14, 50: 0.37, 75: 0.62, 85: 0.79, 90: 0.96,
                       93: 0.98, 95: 1.12, 97: 1.56, 98: 1.56, 99: 1.84},
              "range": {10: 0.34, 25: 0.48, 50: 0.75, 75: 1.15, 85: 1.26, 90: 1.63,
                        93: 1.65, 95: 1.76, 97: 1.78, 98: 1.78, 99: 2.01}},
        "2": {"n": 83,
              "move": {10: 0.09, 25: 0.21, 50: 0.48, 75: 0.76, 85: 1.24, 90: 1.36,
                       93: 1.43, 95: 1.67, 97: 2.43, 98: 2.43, 99: 2.45},
              "range": {10: 0.45, 25: 0.64, 50: 0.92, 75: 1.24, 85: 1.61, 90: 1.89,
                        93: 2.40, 95: 2.67, 97: 2.89, 98: 2.89, 99: 2.97}},
        "3-4": {"n": 85,
              "move": {10: 0.05, 25: 0.16, 50: 0.37, 75: 0.57, 85: 0.70, 90: 0.89,
                       93: 0.98, 95: 0.99, 97: 1.21, 98: 1.26, 99: 1.55},
              "range": {10: 0.35, 25: 0.47, 50: 0.72, 75: 0.88, 85: 1.06, 90: 1.17,
                        93: 1.43, 95: 1.44, 97: 1.50, 98: 1.51, 99: 1.74}},
        "5+": {"n": 81,
              "move": {10: 0.07, 25: 0.17, 50: 0.34, 75: 0.56, 85: 0.64, 90: 0.77,
                       93: 0.83, 95: 0.87, 97: 0.99, 98: 0.99, 99: 0.99},
              "range": {10: 0.33, 25: 0.41, 50: 0.59, 75: 0.84, 85: 0.95, 90: 1.00,
                        93: 1.11, 95: 1.15, 97: 1.23, 98: 1.23, 99: 1.29}},
    },
}

ORDER = ("0", "1", "2", "3-4", "5+")


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
    return (e - (today or datetime.date.today())).days


def _blend(a: dict, b: dict) -> dict:
    return {p: (a[p] + b[p]) / 2.0 for p in PS}


def calibrate(symbol: str, dte: int):
    """(move_table, range_table, bucket, n).  A thin bucket blends neighbours."""
    s = str(symbol).upper()
    if s not in CAL:
        return None, None, None, 0
    table = CAL[s]
    b = bucket_for(dte)
    if b in table and table[b]["n"] >= 20:
        return table[b]["move"], table[b]["range"], b, table[b]["n"]
    i = ORDER.index(b)
    lo = next((table[k] for k in reversed(ORDER[:i]) if k in table), None)
    hi = next((table[k] for k in ORDER[i + 1:] if k in table), None)
    if lo and hi:
        return _blend(lo["move"], hi["move"]), _blend(lo["range"], hi["range"]), b + "*", 0
    src = lo or hi
    if not src:
        return None, None, None, 0
    return src["move"], src["range"], b + "*", src["n"]


def _interp(table: dict, x: float) -> float:
    """P(value <= x), interpolated across the measured percentiles.

    Outside the measured range it saturates to the end percentile rather than
    extrapolating -- the flattering direction is exactly the one that has not
    been observed.
    """
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
    """Live range forecast using the row for the contract's days-to-expiry."""
    from parallax.adapters.market_data.dhan_options import atm_straddle
    from parallax.config.indices import spec as _spec
    d = atm_straddle(symbol, expiry)
    if not d:
        return None
    dte = days_to_expiry(d["expiry"]) if d.get("expiry") else 0
    move_x, range_x, buck, n = calibrate(symbol, dte)
    S = d["straddle"]
    out = dict(d)
    out.update({"step": float(_spec(symbol).step), "dte": dte, "bucket": buck,
                "samples": n, "calibrated": move_x is not None})
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
    """P(the condor finishes in profit) -- uses the BREAK-EVEN, short + credit.

    THIS NUMBER DID NOT SURVIVE WALK-FORWARD.  Do not size a position on it.

    Fitted on data before 2026-05-01 and tested on the four months after, it
    missed in BOTH directions:

        NIFTY   4/-3   predicted 97.6%   realised  88.9%   (-8.7)
        SENSEX  5/-3   predicted 88.6%   realised 100.0%   (+11.4)

    Opposite signs is the signature of noise, not a fixable bias.  The tail this
    depends on is not stable between periods -- one NIFTY test-window expiry
    moved 2.48x the straddle against a training tail that topped out at 1.94x --
    and 65 to 85 expiries simply cannot estimate a 5% tail.

    What IS validated is the BANDS: walk-forward, 47% of sessions landed inside
    p25-p75 and 80% inside p10-p90, exactly as designed.  Use the bands.

    Kept because it is the right shape and correct on the training data; it is
    here as a reference, not as a forecast.
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
