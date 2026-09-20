"""Is the LIVE machine the same strategy as the BACKTEST, or the backtest shifted 60 bars?

Two measurements:
  1. the lag, in bars, between a bar and the moment SMCCrypto.step acts on it;
  2. whether the live 'place' bars line up with the reference generator's place bars
     once shifted by that lag (i.e. identical signals, just late).
  3. the true CAUSAL MINIMUM confirm delay: the smallest n at which the SMC feature row
     for bar j stops changing as the array grows.  CONFIRM must be >= that; anything
     larger is a pure delay, not a causality requirement.
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

from parallax.config.crypto import SMCConfig                 # noqa: E402
from parallax.core.smc_crypto import SMCCrypto, features     # noqa: E402
import strat_c                                               # noqa: E402


def load(path, tail=1400):
    b = pd.read_parquet(path)
    b = b.set_index("open_time") if "open_time" in b.columns else b
    b.index = pd.to_datetime(b.index, utc=True)
    return b[["open", "high", "low", "close", "volume"]].astype(float).sort_index().iloc[-tail:]


def live_lag(b, symbol):
    idx = {ts: i for i, ts in enumerate(b.index)}
    m = SMCCrypto(SMCConfig(symbol=symbol))
    rows = []
    for n in range(401, len(b) + 1):
        for d in m.step(b.iloc[:n]):
            if d.action != "none":
                rows.append((d.action, d.ts, idx[d.ts], n - 1, (n - 1) - idx[d.ts]))
    return pd.DataFrame(rows, columns=["action", "ts", "bar", "acted_at", "lag"])


def minimal_confirm(b, sample=40):
    """Smallest k such that the feature row for bar j is final at array length j+k."""
    worst = 0
    n = len(b)
    for j in range(n - 200, n - 120):
        full = features(b.iloc[:n], 20).iloc[j]
        for k in range(1, 120):
            part = features(b.iloc[:j + k + 1], 20).iloc[j]
            if np.allclose(full.to_numpy(float), part.to_numpy(float), equal_nan=True):
                worst = max(worst, k)
                break
    return worst


def run(path, symbol):
    b = load(path)
    print("== %s (%s)  %d bars  %s -> %s" % (symbol, Path(path).name, len(b),
                                             b.index[0], b.index[-1]))
    live = live_lag(b, symbol)
    places = sorted(live[live.action == "place"].bar.tolist())
    print("   live decisions: %d  (place %d, fill %d, exit %d)"
          % (len(live), (live.action == "place").sum(), (live.action == "fill").sum(),
             (live.action == "exit").sum()))
    if len(live):
        print("   lag bars: min %d median %.0f max %d"
              % (live.lag.min(), live.lag.median(), live.lag.max()))

    feat = strat_c.features(b, 20)
    p, _, _ = strat_c.signals_limit(b, feat, zone="fvg", allow_short=True)
    ref = np.flatnonzero(np.asarray(p) != 0).tolist()
    print("   reference 'place' bars: %d" % len(ref))

    # does live == reference shifted by the measured lag?
    lag = int(live.lag.median()) if len(live) else 0
    shifted = [r + lag for r in ref if r + lag < len(b)]
    common = len(set(places) & set(shifted))
    print("   live places that are exactly reference+%d: %d of %d  (overlap %d)"
          % (lag, common, len(places), len(set(places) & set(ref))))
    print("   => live is %s" % ("the backtest SHIFTED by %d bars (%.0f h)"
                                % (lag, lag) if common >= max(1, int(0.8 * len(places)))
                                else "NOT a simple shift -- investigate"))
    print("   causal minimum confirm: %d bars  (CONFIRM is %d)"
          % (minimal_confirm(b), SMCCrypto().confirm))
    print()


if __name__ == "__main__":
    run(str(Path(r"C:\PrOxyTradingTerminal\.research\eth_xaut_compare\data\ETHUSD_india_1h.parquet")), "ETHUSD")
    run(str(BTC / "data" / "delta" / "BTCUSD_1h.parquet"), "BTCUSD")
