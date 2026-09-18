"""Shared test fixtures."""
import numpy as np
from datetime import datetime, timedelta, timezone

from parallax.contracts import Bar, InstrumentId, InstrumentType
from parallax.core.perception import build_market_state
from parallax.core.context import build_context


def make_bars(n=600, seed=7, start=24900.0, drift=0.0, noise=8.0,
              start_ts=None) -> list[Bar]:
    rng = np.random.default_rng(seed)
    ts = start_ts or datetime(2026, 9, 18, 9, 15, tzinfo=timezone.utc)
    bars = []
    p = start
    for i in range(n):
        o = p
        c = o + drift + rng.normal(0, noise)
        h = max(o, c) + rng.uniform(0, 4)
        l = min(o, c) - rng.uniform(0, 4)
        bars.append(Bar(ts + timedelta(minutes=5 * i), o, h, l, c,
                        float(rng.integers(1000, 5000))))
        p = c
    return bars


def make_state(inst=None, bars=None, timeframe="5m"):
    inst = inst or InstrumentId("NIFTY", InstrumentType.INDEX_FUTURE)
    bars = bars if bars is not None else make_bars()
    ms = build_market_state(inst, bars, timeframe, lookback=600)
    return build_context({timeframe: ms}, timeframe)
