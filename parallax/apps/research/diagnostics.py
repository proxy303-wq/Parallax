"""Trade-level attribution — the "why is this losing" report.

Turns a BacktestResult into the measured facts the corpus found decisive:
exit-reason mix, median favourable/adverse excursion (MFE/MAE), gross R, the
edge ratio (MFE/MAE), and per-regime / per-side expectancy.  No tuning — just
measurement.
"""
from __future__ import annotations

from collections import Counter

from .backtest import BacktestResult


def exit_reason_breakdown(result: BacktestResult) -> dict:
    return dict(Counter(t.exit_reason for t in result.trades))


def excursion_report(result: BacktestResult) -> dict:
    if not result.trades:
        return {"median_mfe_r": None, "median_mae_r": None,
                "median_gross_r": None, "edge_ratio": None, "avg_net_pnl": 0.0}
    mfe = sorted(t.mfe_r for t in result.trades)
    mae = sorted(t.mae_r for t in result.trades)
    gross = sorted(t.r_multiple for t in result.trades)
    n = len(result.trades)
    def med(x):
        return round(x[n // 2], 3)
    mfe_m, mae_m = med(mfe), med(mae)
    return {
        "median_mfe_r": mfe_m,
        "median_mae_r": mae_m,
        "median_gross_r": med(gross),
        "edge_ratio": round(mfe_m / mae_m, 3) if mae_m > 0 else None,
        "avg_net_pnl": round(sum(t.pnl for t in result.trades) / n, 2),
    }


def per_side_report(result: BacktestResult) -> dict:
    out = {}
    for side in ("BUY", "SELL"):
        ts = [t for t in result.trades if t.side == side]
        if not ts:
            continue
        wins = sum(1 for t in ts if t.pnl > 0)
        out[side] = {
            "trades": len(ts),
            "win_rate": round(wins / len(ts), 3),
            "pnl": round(sum(t.pnl for t in ts), 2),
            "avg_gross_r": round(sum(t.r_multiple for t in ts) / len(ts), 3),
        }
    return out


def per_regime_report(result: BacktestResult) -> dict:
    out = {}
    regimes = sorted(set(t.regime for t in result.trades))
    for regime in regimes:
        ts = [t for t in result.trades if t.regime == regime]
        if not ts:
            continue
        out[regime] = {
            "trades": len(ts),
            "win_rate": round(sum(1 for t in ts if t.pnl > 0) / len(ts), 3),
            "pnl": round(sum(t.pnl for t in ts), 2),
        }
    return out


def trade_table(result: BacktestResult) -> list:
    return [t.as_dict() for t in result.trades]


def full_report(result: BacktestResult) -> dict:
    return {
        "metrics": result.metrics,
        "exit_reasons": exit_reason_breakdown(result),
        "excursion": excursion_report(result),
        "per_side": per_side_report(result),
        "per_regime": per_regime_report(result),
    }


def print_report(result: BacktestResult) -> None:
    r = full_report(result)
    m = r["metrics"]
    e = r["excursion"]
    print("trades=" + str(m["trades"]) + " win_rate=" + str(m["win_rate"])
          + " PF=" + str(m["profit_factor"]) + " pnl=" + str(m["total_pnl"])
          + " avgR=" + str(m["avg_r"]))
    print("exit reasons: " + str(r["exit_reasons"]))
    print("MFE(med)=" + str(e["median_mfe_r"]) + "R  MAE(med)="
          + str(e["median_mae_r"]) + "R  grossR(med)=" + str(e["median_gross_r"])
          + "R  edge(MFE/MAE)=" + str(e["edge_ratio"])
          + "  avg_net=" + str(e["avg_net_pnl"]))
    if r["per_side"]:
        print("per side: " + str(r["per_side"]))
    if r["per_regime"]:
        print("per regime: " + str(r["per_regime"]))
