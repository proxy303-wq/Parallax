"""CSV OHLCV loader with normalization and data-quality validation.

Accepts the common 'time,open,high,low,close,volume' schema (as used by the
PrOxyTradingTerminal data directory) plus a few common aliases.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from parallax.contracts import Bar


def _num(x) -> float:
    if isinstance(x, str):
        x = x.replace(",", "").replace("₹", "").strip()
        if x.endswith("M"):
            return float(x[:-1]) * 1_000_000
        if x.endswith("K"):
            return float(x[:-1]) * 1_000
    return float(x)


def _epoch_to_dt(v: float) -> Optional[datetime]:
    if v <= 0:
        return None
    if v > 1e12:      # microseconds
        v = v / 1_000_000.0
    elif v > 1e11:    # milliseconds
        v = v / 1_000.0
    try:
        return datetime.fromtimestamp(v, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _is_number(s: str) -> bool:
    s = s.strip()
    if not s:
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def _parse_ts(x, fmt: Optional[str] = None) -> datetime:
    if isinstance(x, datetime):
        return x
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return _epoch_to_dt(float(x))
    s = str(x).strip()
    if _is_number(s):               # epoch seconds (or ms/us)
        return _epoch_to_dt(float(s))
    if fmt:
        return datetime.strptime(s, fmt)
    return pd.to_datetime(s).to_pydatetime()


def load_ohlcv(path: str, timeframe: str = "5m",
               tz=None, sort: bool = True,
               time_fmt: Optional[str] = None) -> list[Bar]:
    df = pd.read_csv(path)
    cols = {c.strip().lower(): c for c in df.columns}

    def col(*names) -> Optional[str]:
        for n in names:
            if n in cols:
                return cols[n]
        return None

    tcol = col("time", "datetime", "timestamp", "date", "ts")
    ocol = col("open", "o")
    hcol = col("high", "h")
    lcol = col("low", "l")
    ccol = col("close", "c", "price", "ltp", "last")
    vcol = col("volume", "vol", "v")

    if tcol is None or ocol is None or hcol is None or lcol is None or ccol is None:
        raise ValueError(f"could not map OHLCV columns in {path} (found {sorted(cols)})")

    bars: list[Bar] = []
    for _, row in df.iterrows():
        ts = _parse_ts(row[tcol], time_fmt)
        if tz is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=tz)
        bars.append(Bar(
            ts=ts,
            open=_num(row[ocol]),
            high=_num(row[hcol]),
            low=_num(row[lcol]),
            close=_num(row[ccol]),
            volume=_num(row[vcol]) if vcol is not None else 0.0,
        ))

    if sort:
        bars.sort(key=lambda b: b.ts)
    return bars


def validate_bars(bars: list[Bar]) -> dict:
    """Return {issues: [...], duplicate_bars, discontinuities}."""
    issues: list[str] = []
    dupes = 0
    discontinuities = 0
    seen = set()
    prev = None
    for b in bars:
        key = b.ts.isoformat()
        if key in seen:
            dupes += 1
        seen.add(key)
        if prev is not None and b.ts <= prev:
            discontinuities += 1
        prev = b.ts
    if dupes:
        issues.append(f"{dupes} duplicate bars")
    if discontinuities:
        issues.append(f"{discontinuities} non-monotonic timestamps")
    return {"issues": issues, "duplicate_bars": dupes,
            "discontinuities": discontinuities}
