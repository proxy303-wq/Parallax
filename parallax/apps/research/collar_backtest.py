"""Replay a commodity collar over historical futures + option data.

The strategy is a pluggable CollarSpec - put/call distance and when to enter.
For each expiry the collar is entered at the option's real prices and settled
at the option expiry, using the real futures and option daily history, so the
result is a true replay rather than a synthetic-surface approximation.

Work in points (per unit of the future); the lot size converts to rupees only
at the very end, so the same engine tests any size.
"""
from __future__ import annotations
from dataclasses import dataclass

from parallax.adapters.market_data.commodity_chain import contracts
from parallax.adapters.market_data.commodity_history import fetch_historical, series
from parallax.apps.ops.commodity_collar import collar_strikes, payoff
from parallax.config.commodities import spec as cspec


@dataclass
class CollarSpec:
    """A collar strategy.  Swap these fields to test a new design."""
    put_pct: float = 5.0               #: put strike this far below the entry future
    call_pct: float = 5.0              #: call strike this far above the entry future
    entry_days_before_expiry: int = 15  #: enter N trading days before the option expiry
    lots: int = 1                       #: number of contracts (for the rupee figure)


def backtest_expiry(symbol: str, expiry: str, spec: CollarSpec, token: str,
                    client_id: str, lo: str = "2026-01-01", hi: str = "2026-12-31") -> dict | None:
    """One collar on one expiry, entered N days before and settled at the last date."""
    grid, fut_id = contracts(symbol, expiry)
    if not grid or not fut_id:
        return None
    fut = series(fetch_historical(fut_id, "FUTCOM", lo, hi, token, client_id))
    dates = sorted(fut)
    if len(dates) < spec.entry_days_before_expiry + 2:
        return None
    settle_date = dates[-1]
    entry_date = dates[len(dates) - 1 - spec.entry_days_before_expiry]
    F0 = fut[entry_date]

    strikes = sorted(grid)
    kp, kc = collar_strikes(F0, strikes, spec.put_pct, spec.call_pct)
    if kp is None or kc is None:
        return None
    put_id, call_id = grid[kp].get("PE"), grid[kc].get("CE")
    if not put_id or not call_id:
        return None

    ps = series(fetch_historical(put_id, "OPTFUT", lo, hi, token, client_id))
    cs = series(fetch_historical(call_id, "OPTFUT", lo, hi, token, client_id))
    if entry_date not in ps or entry_date not in cs:
        return None

    p, c = ps[entry_date], cs[entry_date]
    settle = fut[settle_date]
    pnl = payoff(F0, kp, kc, p - c, settle)
    lot = cspec(symbol).lot or 1
    return {
        "symbol": symbol, "expiry": expiry, "entry": entry_date, "settle": settle_date,
        "F0": round(F0, 2), "put_strike": kp, "call_strike": kc,
        "put_cost": round(p, 2), "call_credit": round(c, 2), "net": round(p - c, 2),
        "settle_price": round(settle, 2),
        "pnl_points": round(pnl, 2), "pnl_rs": round(pnl * lot * spec.lots, 2),
    }


def stats(trades: list[dict]) -> dict:
    """Aggregate a list of trade dicts."""
    if not trades:
        return {"n": 0}
    pnls = [t["pnl_points"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    return {
        "n": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades),
        "total_points": round(sum(pnls), 2),
        "avg_points": round(sum(pnls) / len(pnls), 2),
        "best": max(pnls),
        "worst": min(pnls),
        "total_rs": round(sum(t["pnl_rs"] for t in trades), 2),
    }
