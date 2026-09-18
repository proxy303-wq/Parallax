"""Model backtest: weekly short iron condor on NIFTY / FINNIFTY, hold to expiry.

Synthesizes option premiums from the underlying via Black-Scholes (realised vol
as the IV proxy).  Validates the strategy MECHANICS (theta, strike selection,
hold-to-expiry payoff) but NOT the real-world vol-risk-premium edge: a true
backtest needs 3 months of historical option chains (only 5 days are captured
locally, and the Dhan Data API subscription is off).

run_options_backtest() returns per-symbol results for IV=realised and
IV=realised x 1.15 so the edge's source is visible.
"""
from __future__ import annotations

import math
import statistics
from collections import OrderedDict
from datetime import timedelta

from parallax.adapters.market_data.csv_loader import load_ohlcv
from parallax.core.options import optmath as om

NIFTY_CSV = r"C:\PrOxyTradingTerminal\data\NIFTY_5m.csv"
FINNIFTY_CSV = r"C:\PrOxyTradingTerminal\data\FINNIFTY_5m.csv"


def daily_bars(bars):
    days = OrderedDict()
    for b in bars:
        days[b.ts.date()] = b
    return list(days.values())


def realized_vol(closes):
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    if len(rets) < 10:
        return 0.13
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def _leg_pnl(spot_exp, strike, flag, side, premium):
    intrinsic = max(0.0, spot_exp - strike) if flag == "c" else max(0.0, strike - spot_exp)
    return -side * premium + side * intrinsic


def run_iron_condor(symbol, csv_path, months=3, dte=7, vol_premium=0.0,
                    spread_steps=3, delta=0.16, step=50.0, lot=None):
    bars = load_ohlcv(csv_path, "5m")
    daily = daily_bars(bars)
    start = daily[-1].ts.date() - timedelta(days=months * 30)
    daily = [b for b in daily if b.ts.date() >= start]
    closes = [b.close for b in daily]
    lot = lot or (65 if symbol == "NIFTY" else 60)
    trades = []
    i = 0
    while i < len(daily) - dte:
        spot = daily[i].close
        sigma = realized_vol(closes[max(0, i - 20):i + 1]) * (1.0 + vol_premium)
        T = dte / 365.0
        put_k = om.round_to_step(om.delta_strike(spot, sigma, dte, delta, "put") or spot, step, "floor")
        call_k = om.round_to_step(om.delta_strike(spot, sigma, dte, delta, "call") or spot, step, "ceil")
        hedge_p = om.round_to_step(put_k - spread_steps * step, step, "floor")
        hedge_c = om.round_to_step(call_k + spread_steps * step, step, "ceil")
        legs = [
            (put_k, "p", -1, om.bs_price(spot, put_k, T, sigma, "p")),
            (call_k, "c", -1, om.bs_price(spot, call_k, T, sigma, "c")),
            (hedge_p, "p", 1, om.bs_price(spot, hedge_p, T, sigma, "p")),
            (hedge_c, "c", 1, om.bs_price(spot, hedge_c, T, sigma, "c")),
        ]
        spot_exp = daily[i + dte].close
        pnl_pts = sum(_leg_pnl(spot_exp, k, f, s, p) for k, f, s, p in legs)
        credit = sum(p for k, f, s, p in legs if s < 0) - sum(p for k, f, s, p in legs if s > 0)
        trades.append({
            "entry_date": str(daily[i].ts.date()), "spot": round(spot, 1),
            "expiry_spot": round(spot_exp, 1), "sigma": round(sigma, 3),
            "put_k": put_k, "call_k": call_k, "credit": round(credit, 2),
            "max_loss": round(spread_steps * step - credit, 2),
            "pnl_inr": round(pnl_pts * lot, 0),
        })
        i += 5
    wins = [t for t in trades if t["pnl_inr"] > 0]
    return {
        "symbol": symbol, "vol_premium": vol_premium, "n": len(trades),
        "win_rate": round(len(wins) / len(trades), 3) if trades else 0.0,
        "total_pnl": round(sum(t["pnl_inr"] for t in trades), 0),
        "avg_credit": round(statistics.mean(t["credit"] * lot for t in trades), 0) if trades else 0,
        "worst": round(min(t["pnl_inr"] for t in trades), 0) if trades else 0,
        "best": round(max(t["pnl_inr"] for t in trades), 0) if trades else 0,
        "trades": trades,
    }




def run_long_directional(symbol, csv_path, months=3, dte=7, vol_premium=0.0,
                         delta=0.40, step=50.0, lot=None):
    """Model backtest: weekly directional LONG option (buy call/put) held to
    expiry.  Bias = SMA20 vs SMA50 (the ICT HTF bias).  Buying is a debit and
    only wins when the directional move beats the premium paid."""
    bars = load_ohlcv(csv_path, "5m")
    daily = daily_bars(bars)
    start = daily[-1].ts.date() - timedelta(days=months * 30)
    start_idx = next(i for i, b in enumerate(daily) if b.ts.date() >= start)
    closes = [b.close for b in daily]   # FULL history -> SMA warm-up
    lot = lot or (65 if symbol == "NIFTY" else 60)
    trades = []
    i = max(50, start_idx)
    while i < len(daily) - dte:
        spot = daily[i].close
        sma20 = sum(closes[i - 19:i + 1]) / 20.0
        sma50 = sum(closes[i - 49:i + 1]) / 50.0
        bullish = sma20 > sma50
        sigma = realized_vol(closes[i - 20:i + 1]) * (1.0 + vol_premium)
        T = dte / 365.0
        flag = "c" if bullish else "p"
        k = om.delta_strike(spot, sigma, dte, delta, "call" if bullish else "put") or spot
        strike = om.round_to_step(k, step, "nearest")
        premium = om.bs_price(spot, strike, T, sigma, flag)
        spot_exp = daily[i + dte].close
        intrinsic = max(0.0, spot_exp - strike) if flag == "c" else max(0.0, strike - spot_exp)
        trades.append({
            "entry_date": str(daily[i].ts.date()), "spot": round(spot, 1),
            "bias": "CALL" if bullish else "PUT", "strike": strike,
            "premium": round(premium, 2), "expiry_spot": round(spot_exp, 1),
            "pnl_inr": round((intrinsic - premium) * lot, 0),
        })
        i += 5
    wins = [t for t in trades if t["pnl_inr"] > 0]
    return {
        "symbol": symbol, "vol_premium": vol_premium, "n": len(trades),
        "win_rate": round(len(wins) / len(trades), 3) if trades else 0.0,
        "total_pnl": round(sum(t["pnl_inr"] for t in trades), 0),
        "avg_premium": round(statistics.mean(t["premium"] * lot for t in trades), 0) if trades else 0,
        "worst": round(min(t["pnl_inr"] for t in trades), 0) if trades else 0,
        "best": round(max(t["pnl_inr"] for t in trades), 0) if trades else 0,
        "trades": trades,
    }


def run_options_backtest(symbols=("NIFTY", "FINNIFTY"), months=3, vol_premiums=(0.0, 0.15)):
    paths = {"NIFTY": NIFTY_CSV, "FINNIFTY": FINNIFTY_CSV}
    out = []
    for sym in symbols:
        for vp in vol_premiums:
            out.append(run_iron_condor(sym, paths[sym], months=months, vol_premium=vp))
    return out
