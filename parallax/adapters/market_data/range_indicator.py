"""How far will the market move today?  Read it off the ATM straddle.

Calibrated on the +-10 ladder, 2025-01-05 .. 2026-09-01, entry 09:20 on the
expiry day, measured to the close:

    NIFTY   83 expiries      SENSEX  79 expiries

Every number below is a multiple of the ATM STRADDLE at 09:20, which is the
market's own expected move and needs no vol model.

The single most useful fact in here: the market ROAMS much further than it
ENDS UP.  On NIFTY the day's high-low range is a median 1.15x the straddle,
while the net move from entry to settlement is a median 0.64x.  A condor is
settled by the net move and paid against the range, which is the whole reason
the trade exists.

    NIFTY    range x: p10 0.50  p25 0.75  p50 1.15  p75 1.72  p90 2.10  p95 2.54
             move  x: p10 0.11  p25 0.19  p50 0.64  p75 0.91  p90 1.50  p95 1.94
    SENSEX   range x: p10 0.31  p25 0.61  p50 1.22  p75 1.76  p90 2.30  p95 2.46
             move  x: p10 0.05  p25 0.20  p50 0.52  p75 0.99  p90 1.42  p95 2.10

Cross-check against what the book actually did: NIFTY short_off 4 is 200 pts on
a ~172 pt straddle = 1.16x, which sits between p75 and p90 and therefore
predicts a win rate near 0.87 -- the measured figure.  SENSEX short_off 5 is
500 pts on ~622 = 0.80x, below p75, predicting ~0.75 against a measured 0.74.
The table predicts the results it produced, which is the only test of a
forecast that matters.

BANKNIFTY and BANKEX are NOT calibrated here.  BANKNIFTY is monthly (19
expiries in the window) and BANKEX's chain serves bad prints at the strikes we
trade -- a 62,800 put quoted at 55,003 -- so neither is trustworthy yet.
"""
from __future__ import annotations

#: percentile -> multiple of the 09:20 ATM straddle
NET_MOVE_X = {
    "NIFTY":  {10: 0.11, 25: 0.19, 50: 0.64, 75: 0.91, 90: 1.50, 95: 1.94},
    "SENSEX": {10: 0.05, 25: 0.20, 50: 0.52, 75: 0.99, 90: 1.42, 95: 2.10},
}
DAY_RANGE_X = {
    "NIFTY":  {10: 0.50, 25: 0.75, 50: 1.15, 75: 1.72, 90: 2.10, 95: 2.54},
    "SENSEX": {10: 0.31, 25: 0.61, 50: 1.22, 75: 1.76, 90: 2.30, 95: 2.46},
}
PS = (10, 25, 50, 75, 90, 95)


def _interp(table: dict, x: float) -> float:
    """P(move <= x * straddle), by linear interpolation over the percentiles."""
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


def _mult(table: dict, p: int) -> float:
    return table.get(p, 0.0)


def calibrate(symbol: str) -> tuple:
    s = str(symbol).upper()
    if s not in NET_MOVE_X:
        return None, None
    return NET_MOVE_X[s], DAY_RANGE_X[s]


def forecast(symbol: str, expiry: str | None = None) -> dict | None:
    """Live range forecast from the current ATM straddle.

    Returns the projected day range and net move, the percentile bands, and -
    the actionable part - the odds that a given strike offset holds.
    """
    from parallax.adapters.market_data.dhan_options import atm_straddle
    from parallax.config.indices import spec as _spec
    d = atm_straddle(symbol, expiry)
    if not d:
        return None
    step = float(_spec(symbol).step)
    move_x, range_x = calibrate(symbol)
    S = d["straddle"]
    out = dict(d)
    out["step"] = step
    out["proj_net_move"] = _mult(move_x, 50) * S if move_x else 0.0
    out["proj_range"] = _mult(range_x, 50) * S if range_x else 0.0
    out["move_bands"] = {p: _mult(move_x, p) * S for p in PS} if move_x else {}
    out["range_bands"] = {p: _mult(range_x, p) * S for p in PS} if range_x else {}
    out["calibrated"] = move_x is not None
    return out


def prob_move_within(symbol: str, pts: float, straddle: float) -> float | None:
    """P(|net move| <= pts), from the calibrated table."""
    move_x, _ = calibrate(symbol)
    if not move_x or straddle <= 0:
        return None
    return _interp(move_x, float(pts) / float(straddle))


def prob_profit(symbol: str, short_pts: float, credit_pts: float,
                straddle: float) -> float | None:
    """P(the condor finishes in profit).

    NOT the same as P(the shorts hold).  A condor is profitable out to its
    BREAK-EVEN, which is short distance PLUS the credit collected -- and that
    difference is the whole point of selling a spread rather than a naked
    option.  Using short_pts alone under-predicts the win rate by the credit:
    SENSEX 5/-3 reads 0.65 that way against 0.74 measured, and 0.74 once the
    ~100-point credit is added back.
    """
    return prob_move_within(symbol, float(short_pts) + float(credit_pts), straddle)


def strikes_for_profit(symbol: str, straddle: float, credit_pts: float,
                       target: float) -> int | None:
    """The smallest strike offset whose modelled PROFIT rate clears target."""
    from parallax.config.indices import spec as _spec
    move_x, _ = calibrate(symbol)
    if not move_x or straddle <= 0:
        return None
    step = float(_spec(symbol).step)
    for so in range(1, 21):
        if _interp(move_x, (so * step + float(credit_pts)) / straddle) >= target:
            return so
    return None
