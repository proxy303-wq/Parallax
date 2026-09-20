"""Run the FROZEN finalized configuration over one window -- no walk-forward, no selection.

The configuration is the one the walk-forward settled on (REPORT.md 13.4):
    stop floor 1.5 ATR, 5 ATR chandelier trail (no fixed target), long+short,
    resting limit entries at the FVG edge (ttl 20), risk 0.75%/trade, 1x.

This is the only honest way to test a symbol whose history is too short for monthly
walk-forward folds -- it uses the parameters chosen on BTC, never tuned on the new symbol.

Usage: python src/run_frozen.py <parquet> <label> [--since ISO] [--until ISO]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run_wfo import build_specs, delta_costs, load   # noqa: E402
from engine import run, metrics                      # noqa: E402

FROZEN = (1.5, "trail5", True)      # (stop floor, exit key, allow_short)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("parquet")
    ap.add_argument("label")
    ap.add_argument("--since", default=None)
    ap.add_argument("--until", default=None)
    ap.add_argument("--cap-usd", type=float, default=5_00_000 / 88.0)
    ap.add_argument("--lev", type=float, default=1.0)
    a = ap.parse_args()

    b = load(a.parquet)
    if a.since:
        b = b.loc[pd.Timestamp(a.since, tz="UTC"):]
    if a.until:
        b = b.loc[:pd.Timestamp(a.until, tz="UTC")]
    if len(b) < 300:
        print("%s: only %d bars in window -- too short" % (a.label, len(b)))
        return

    specs = build_specs(b, a.lev)
    spec = specs[FROZEN]
    res = run(b, spec, a.cap_usd, delta_costs())
    m = metrics(res, b, a.cap_usd, label=a.label)
    tr = res["trades"]
    bh = 100 * (b.close.iloc[-1] / b.close.iloc[0] - 1)
    bhdd = 100 * (b.close / b.close.cummax() - 1).min()

    print("== %s ==" % a.label)
    print("  window        %s -> %s  (%d bars, %.1f months)"
          % (b.index[0], b.index[-1], len(b), len(b) / 24 / 30.4))
    print("  trades        %d" % len(tr))
    if len(tr):
        rr = tr.ret_on_risk.dropna()
        se = rr.std() / np.sqrt(len(rr)) if len(rr) > 1 else np.nan
        print("  total return  %+.2f%%" % m["total_ret_pct"])
        print("  max DD        %.2f%%" % m["max_dd_pct"])
        print("  win rate      %.1f%%" % m["win_rate_pct"])
        print("  expectancy    %+.3fR   (t=%s, 95%% CI [%+.3f, %+.3f])"
              % (rr.mean(), round(rr.mean() / se, 2) if se == se else "n/a",
                 rr.mean() - 1.96 * se, rr.mean() + 1.96 * se))
        print("  profit factor %.2f" % m["profit_factor"])
        print("  exposure      %.1f%%" % m["exposure_pct"])
        print("  avg hold      %.0f bars" % tr.bars.mean())
        print("  exit mix      %s" % tr.reason.value_counts().to_dict())
    print("  limit fill    %d/%d = %.1f%%"
          % (res["limit_filled"], res["limit_placed"],
             100 * res["limit_filled"] / res["limit_placed"] if res["limit_placed"] else 0))
    print("  UNDERLYING    %+.2f%%   (max DD %.2f%%)" % (bh, bhdd))


if __name__ == "__main__":
    main()
