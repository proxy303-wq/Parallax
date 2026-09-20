"""Does the deployed live worker implement the validated strategy?

The strategy only reads the SIGN of a structure break (BOS and CHoCH both set the same
bias), so a BOS/CHoCH relabel is harmless while a sign flip is not.  This checks the
combined sign series, then runs the live machine and the backtest over the same bars and
compares the resulting trades.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(r"C:\Parallax")
BTC = Path(r"C:\PrOxyTradingTerminal\.research\btc_jas_compare")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(BTC / "src"))
sys.path.insert(0, str(BTC / "repos" / "smart-money-concepts"))

from parallax.config.crypto import SMCConfig, USD_INR          # noqa: E402
from parallax.core.smc_crypto import SMCCrypto                 # noqa: E402
from parallax.core.smc_crypto import features as live_features  # noqa: E402
from engine import run, Costs, Spec, atr_wilder                  # noqa: E402
import strat_c                                                  # noqa: E402

GST, SLIP = 1.18, 0.0001


def delta_costs():
    return Costs(fee=0, slip=SLIP, fee_open=0.0002 * GST, fee_close=0.0005 * GST,
                 fee_close_limit=0.0002 * GST, funding_per_bar=0.0001 / 8)


def sign(a):
    """Combined structure sign the strategy actually consumes."""
    return np.where((a == 1), 1, np.where((a == -1), -1, 0))


def load():
    b = pd.read_parquet(BTC / "data" / "delta" / "BTCUSD_1h.parquet")
    b = b.set_index("open_time")
    b.index = pd.to_datetime(b.index, utc=True)
    return b[["open", "high", "low", "close", "volume"]].astype(float).sort_index().iloc[-1500:]


def main():
    b = load()
    lf, bf = live_features(b, 20), strat_c.features(b, 20)

    print("== 1. the structure SIGN the strategy reads ==")
    ls = sign(lf["bos"].fillna(0).to_numpy()) | sign(lf["choch"].fillna(0).to_numpy())
    bs = sign(bf["bos"].fillna(0).to_numpy()) | sign(bf["choch"].fillna(0).to_numpy())
    print("   live  structure events: %d" % int((ls != 0).sum()))
    print("   bt    structure events: %d" % int((bs != 0).sum()))
    print("   agreement on the SIGN : %.4f%%" % (100.0 * (ls == bs).mean()))
    print("   sign flips            : %d" % int(((ls != 0) & (bs != 0) & (ls != bs)).sum()))

    print()
    print("== 2. live machine vs backtest, same bars ==")
    m = SMCCrypto(SMCConfig(symbol="BTCUSD"))
    eq = 800000.0
    live_trades = []
    idx = list(b.index)
    for n in range(401, len(b) + 1):
        for d in m.step(b.iloc[:n]):
            if d.action == "fill":
                qty = m.size(eq, d.price, d.stop)
                if qty > 0:
                    risk = qty * m.prod.contract_value * abs(d.price - d.stop) * USD_INR
                    m.open_position(d.side, d.price, qty, d.stop, risk, ts=d.ts)
            elif d.action == "exit":
                live_trades.append((d.ts, d.side, d.exit_price, d.pnl, d.reason))
    print("   LIVE   : %d places, %d closed trades, net Rs %+.0f"
          % (len(live_trades) and 1 or 0, len(live_trades), sum(t[3] for t in live_trades)))

    feat = strat_c.features(b, 20)
    atr = atr_wilder(b, 14)
    p, lm, ls_ = strat_c.signals_limit(b, feat, zone="fvg", allow_short=True)
    spec = Spec(entries=pd.Series(p, index=b.index), exit_signal=pd.Series(False, index=b.index),
                atr=atr, stop_price=pd.Series(ls_, index=b.index), limit_entries=True,
                limit_price=pd.Series(lm, index=b.index), limit_ttl=20, trail_mult=5.0,
                target_R=None, risk_pct=0.0075, allow_short=True, stop_atr_floor=1.5)
    res = run(b, spec, eq, delta_costs())
    tr = res["trades"]
    print("   BACKTEST: %d limit orders, %d closed trades, net USD %+.2f"
          % (res["limit_placed"], len(tr), tr.pnl.sum() if len(tr) else 0.0))

    if len(tr) and live_trades:
        print("   live exits at:", [str(t[0]) for t in live_trades[:5]])
        print("   bt   exits at:", [str(t) for t in tr.exit_time.head(5).tolist()])


if __name__ == "__main__":
    main()
