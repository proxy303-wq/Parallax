"""Walk-forward replication of the FINALIZED BTC strategy, run on any symbol's 1h bars.

Faithful port of btc_jas_compare/src/run_refine_wfo3.py -- the script behind the recorded
headline (REPORT.md 13.4).  Verified: run unchanged today it produces 451 trades and
+173.51% compounded (the report's recorded 427 / +170.40% came from an earlier snapshot of
data/delta/BTCUSD_1h.parquet), and this port reproduces that fold-for-fold.

Nothing about the strategy, the config grid, the costs or the selection rule is changed --
only the price series is swapped.

Protocol (unchanged):
  * features  : strat_c.features(bars, 20)         causal SMC, BOS/CHoCH + FVG, lagged
  * entries   : strat_c.signals_limit(zone="fvg")  resting limit at the zone edge, ttl 20
  * grid      : stop floor {1.5,2.0,3.0} x exit {R3,R5,trail5,R10+trail5} x {long, L+S}
  * selection : best IN-SAMPLE total return, requiring >= 30 in-sample trades
  * folds     : monthly, expanding in-sample, OOS = the next calendar month (Apr 2024+)
  * costs     : maker 2.36bp entry, taker 5.90bp stop, maker target, 1bp slippage,
                funding 0.01%/8h, 18% GST included
  * risk      : 0.75% of equity per trade, 1x (no leverage), capital Rs 5,00,000

Usage: python src/run_wfo.py <parquet> <label> [--cap-usd N] [--lev N] [--start YYYY-MM-01]
"""
from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
BTC = Path(r"C:\PrOxyTradingTerminal\.research\btc_jas_compare")
sys.path.insert(0, str(BTC / "src"))
sys.path.insert(0, str(BTC / "repos" / "smart-money-concepts"))

from engine import run, metrics, Costs, Spec, atr_wilder   # noqa: E402
import strat_c                                              # noqa: E402

GST, SLIP = 1.18, 0.0001
EXITS = {"R3": (3.0, None), "R5": (5.0, None),
         "trail5": (None, 5.0), "R10+trail5": (10.0, 5.0)}


def delta_costs() -> Costs:
    """Delta India schedule taken from the account's own fills (REPORT 11.1 / 14.1)."""
    return Costs(fee=0, slip=SLIP,
                 fee_open=0.0002 * GST,         # maker: resting limit entry
                 fee_close=0.0005 * GST,        # taker: stop exit
                 fee_close_limit=0.0002 * GST,  # maker: target exit
                 funding_per_bar=0.0001 / 8)


def load(path: str) -> pd.DataFrame:
    b = pd.read_parquet(path)
    if "open_time" in b.columns:
        b = b.set_index("open_time")
    b.index = pd.to_datetime(b.index, utc=True)
    b = b[["open", "high", "low", "close", "volume"]].astype(float)
    return b[~b.index.duplicated(keep="first")].sort_index()


def build_specs(b: pd.DataFrame, leverage: float = 1.0) -> dict:
    feat = strat_c.features(b, 20)
    atr = atr_wilder(b, 14)
    specs = {}
    for short in (False, True):
        p, lm, ls = strat_c.signals_limit(b, feat, zone="fvg", allow_short=short)
        for floor, (ek, (tgt, trl)) in itertools.product([1.5, 2.0, 3.0], EXITS.items()):
            specs[(floor, ek, short)] = Spec(
                entries=pd.Series(p, index=b.index),
                exit_signal=pd.Series(False, index=b.index),
                atr=atr, stop_price=pd.Series(ls, index=b.index),
                limit_entries=True, limit_price=pd.Series(lm, index=b.index),
                limit_ttl=20, trail_mult=trl, target_R=tgt,
                risk_pct=0.0075, allow_short=short, stop_atr_floor=floor,
                max_leverage=leverage)
    return specs


def walk_forward(b, specs, cap, start, min_is=1500, min_trades=30):
    costs = delta_costs()
    months = pd.date_range(start, b.index[-1].tz_convert("UTC").tz_localize(None),
                           freq="MS").tz_localize("UTC")
    rows, trades = [], []
    for m in months:
        e = min(m + pd.DateOffset(months=1), b.index[-1])
        IS = b.loc[:m].iloc[:-1]
        if len(IS) < min_is:
            continue
        scored = []
        for k, sp in specs.items():
            mm = metrics(run(IS, sp, cap, costs), IS, cap)
            if mm["n_trades"] < min_trades:
                continue
            scored.append((mm["total_ret_pct"], k, mm["expectancy_R"]))
        if not scored:
            continue
        scored.sort(key=lambda x: x[0], reverse=True)
        s, k, isr = scored[0]
        OOS = b.loc[m:e]
        res = run(OOS, specs[k], cap, costs)
        mm = metrics(res, OOS, cap)
        rows.append(dict(month=m.strftime("%Y-%m"), floor=k[0], exit=k[1],
                         dir="L+S" if k[2] else "long", is_ret=s, is_R=isr,
                         oos_R=mm["expectancy_R"], ret=mm["total_ret_pct"],
                         n=mm["n_trades"], dd=mm["max_dd_pct"],
                         pf=mm.get("profit_factor", np.nan),
                         fill=(res["limit_filled"] / res["limit_placed"]
                               if res["limit_placed"] else np.nan)))
        if len(res["trades"]):
            t = res["trades"].copy()
            t["month"] = m.strftime("%Y-%m")
            trades.append(t)
    return pd.DataFrame(rows), (pd.concat(trades, ignore_index=True) if trades else pd.DataFrame())


def report(df, tr, b, cap, label, start_month):
    eq = (1 + df.ret / 100).cumprod()
    tot = eq.iloc[-1] - 1
    dd = (eq / eq.cummax() - 1).min()
    mr = df.ret / 100
    w = b.loc[pd.Timestamp(start_month, tz="UTC"):]
    out = {
        "book": label,
        "folds": len(df),
        "compounded_pct": round(100 * tot, 2),
        "final_inr": round(500000 * (1 + tot), 0),
        "maxDD_pct": round(100 * dd, 2),
        "sharpe_m": (round(mr.mean() / mr.std() * np.sqrt(12), 2) if mr.std() else np.nan),
        "pos_months": "%d/%d" % (int((df.ret > 0).sum()), len(df)),
        "trades": int(df.n.sum()),
        "fill_rate": round(float(df.fill.mean()), 3),
        "underlying_pct": round(100 * (w.close.iloc[-1] / w.close.iloc[0] - 1), 2),
        "underlying_maxDD_pct": round(100 * (w.close / w.close.cummax() - 1).min(), 2),
    }
    if len(tr):
        rr = tr.ret_on_risk.dropna()
        n = len(rr)
        se = rr.std() / np.sqrt(n)
        out.update(oos_expectancy_R=round(float(rr.mean()), 3),
                   oos_t=(round(float(rr.mean() / se), 2) if se else np.nan),
                   ci_low=round(float(rr.mean() - 1.96 * se), 3),
                   ci_high=round(float(rr.mean() + 1.96 * se), 3),
                   win_rate_pct=round(100 * float((tr.pnl > 0).mean()), 1),
                   exit_mix=str(tr.reason.value_counts().to_dict()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("parquet")
    ap.add_argument("label")
    ap.add_argument("--cap-usd", type=float, default=5_00_000 / 88.0)
    ap.add_argument("--lev", type=float, default=1.0)
    ap.add_argument("--start", default="2024-04-01")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    b = load(a.parquet)
    gap = int((b.index.to_series().diff().dt.total_seconds().div(3600) - 1).clip(lower=0).sum())
    print("=== %s ===" % a.label)
    print("bars %d   %s -> %s" % (len(b), b.index[0], b.index[-1]))
    print("missing hourly slots %d   zero-volume %d (%.1f%%)"
          % (gap, int((b.volume <= 0).sum()), 100 * (b.volume <= 0).mean()))

    specs = build_specs(b, a.lev)
    df, tr = walk_forward(b, specs, a.cap_usd, a.start)
    if df.empty:
        print("NO FOLDS -- not enough in-sample history for this series")
        return

    pd.set_option("display.width", 250)
    print("\n--- folds ---")
    print(df.round(3).to_string(index=False))
    print("\n--- selected configuration ---")
    print("  exit : %s" % df.exit.value_counts().to_dict())
    print("  floor: %s" % df.floor.value_counts().to_dict())
    print("  dir  : %s" % df.dir.value_counts().to_dict())

    print("\n--- year by year (out-of-sample) ---")
    d2 = df.copy(); d2["year"] = d2.month.str[:4]
    for y, g in d2.groupby("year"):
        ye = (1 + g.ret / 100).cumprod()
        ydd = (ye / ye.cummax() - 1).min()
        yy = b.loc[g.month.iloc[0]:]
        yy = yy.loc[:min(pd.Timestamp(g.month.iloc[-1] + "-01", tz="UTC")
                         + pd.DateOffset(months=1), b.index[-1])]
        print("  %s: book %+7.2f%%  maxDD %6.2f%%  months +%d/%d  trades %3d  |  underlying %+8.2f%%"
              % (y, 100 * (ye.iloc[-1] - 1), 100 * ydd, int((g.ret > 0).sum()), len(g),
                 int(g.n.sum()), 100 * (yy.close.iloc[-1] / yy.close.iloc[0] - 1)))

    res = report(df, tr, b, a.cap_usd, a.label, df.month.iloc[0])
    print("\n--- headline ---")
    for k, v in res.items():
        print("  %-22s %s" % (k, v))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(a.out, index=False)
        print("\nfolds written: %s" % a.out)


if __name__ == "__main__":
    main()
