"""Unmodified re-run of the finalized walk-forward, to establish the baseline reproducible TODAY.
Only the import paths and the output directory differ from the original script.
"""
"""The selection metric matters: pick by IS *equity growth*, not per-trade expectancy.
A 0.56R edge on 212 trades beats a 0.63R edge on 142 trades -- expectancy alone misses that."""
import sys, itertools
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, r"C:\PrOxyTradingTerminal\.research\btc_jas_compare\src")
sys.path.insert(0, r"C:\PrOxyTradingTerminal\.research\btc_jas_compare\repos\smart-money-concepts")
from engine import run, metrics, Costs, Spec, atr_wilder
import strat_c

BASE = Path(r"C:\PrOxyTradingTerminal\.research\btc_jas_compare")
CAP = 5_00_000/88.0
GST, SLIP = 1.18, 0.0001
def costs():
    return Costs(fee=0, slip=SLIP, fee_open=0.0002*GST, fee_close=0.0005*GST,
                 fee_close_limit=0.0002*GST, funding_per_bar=0.0001/8)
b = pd.read_parquet(BASE/"data"/"delta"/"BTCUSD_1h.parquet").set_index("open_time")
feat = strat_c.features(b, 20); atr = atr_wilder(b, 14)
EXITS = {"R3": (3.0, None), "R5": (5.0, None), "trail5": (None, 5.0), "R10+trail5": (10.0, 5.0)}
SPECS = {}
for floor, (ek, (tgt, trl)), short in itertools.product([1.5, 2.0, 3.0], EXITS.items(), [False, True]):
    p, lm, ls = strat_c.signals_limit(b, feat, zone="fvg", allow_short=short)
    SPECS[(floor, ek, short)] = Spec(
        entries=pd.Series(p, index=b.index), exit_signal=pd.Series(False, index=b.index), atr=atr,
        stop_price=pd.Series(ls, index=b.index), limit_entries=True,
        limit_price=pd.Series(lm, index=b.index), limit_ttl=20, trail_mult=trl, target_R=tgt,
        risk_pct=0.0075, allow_short=short, stop_atr_floor=floor)

months = pd.date_range("2024-04-01", b.index[-1].tz_convert("UTC").tz_localize(None), freq="MS").tz_localize("UTC")
def pick(metric):
    rows = []
    for m in months:
        e = min(m + pd.DateOffset(months=1), b.index[-1]); IS = b.loc[:m].iloc[:-1]
        if len(IS) < 1500: continue
        scored = []
        for k, sp in SPECS.items():
            mm = metrics(run(IS, sp, CAP, costs()), IS, CAP)
            if mm["n_trades"] < 30: continue
            if metric == "expectancy": s = mm["expectancy_R"] if np.isfinite(mm["expectancy_R"]) else -9
            elif metric == "is_return": s = mm["total_ret_pct"]
            else: s = (mm["total_ret_pct"]/abs(mm["max_dd_pct"])) if mm["max_dd_pct"] < 0 else mm["total_ret_pct"]
            scored.append((s, k, mm["expectancy_R"]))
        if not scored: continue
        scored.sort(key=lambda x: x[0], reverse=True)
        s, k, isr = scored[0]
        OOS = b.loc[m:e]; res = run(OOS, SPECS[k], CAP, costs()); mm = metrics(res, OOS, CAP)
        rows.append(dict(month=m.strftime("%Y-%m"), floor=k[0], exit=k[1],
                         dir="L+S" if k[2] else "long", is_R=isr, oos_R=mm["expectancy_R"],
                         ret=mm["total_ret_pct"], n=mm["n_trades"], dd=mm["max_dd_pct"]))
    return pd.DataFrame(rows)

pd.set_option("display.width", 240)
summary = []
for metric, label in (("expectancy","IS expectancy_R (old)"), ("is_return","IS total return"),
                      ("calmar","IS Calmar (return/maxDD)")):
    df = pick(metric)
    eq = (1+df.ret/100).cumprod(); tot = eq.iloc[-1]-1; dd = (eq/eq.cummax()-1).min(); mr = df.ret/100
    summary.append(dict(selection=label, compounded_pct=100*tot, inr=5_00_000*tot, maxDD_pct=100*dd,
                        calmar=100*tot/abs(100*dd), pos_months=f"{int((df.ret>0).sum())}/{len(df)}",
                        trades=int(df.n.sum()), sharpe=mr.mean()/mr.std()*np.sqrt(12),
                        is_R=df.is_R.mean(), oos_R=df.oos_R.mean()))
    print(f"\n--- selection by {label} ---")
    print(f"  picks: exit {df.exit.value_counts().to_dict()}  floor {df.floor.value_counts().to_dict()}")
    df.to_csv(Path(r"C:\PrOxyTradingTerminal\.research\eth_xaut_compare\out")/f"verify_original_{metric}.csv", index=False)
print("\n===== SUMMARY: same data, same configs, different selection objective =====")
s = pd.DataFrame(summary)
print(s.round(2).to_string(index=False))
w = b.loc[months[0]:]
print(f"\nBENCHMARK buy&hold: {100*(w.close.iloc[-1]/w.close.iloc[0]-1):+.2f}%  maxDD {100*(w.close/w.close.cummax()-1).min():.2f}%")