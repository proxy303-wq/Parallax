"""Deterministic, collision-resistant ID generation.

IDs carry a UTC timestamp so every decision, order, hypothesis and reasoning
trace is sortable and greppable in the audit trail.  The design-doc example is
'dec_20260918_001842' — prefix + YYYYMMDD_HHMMSS + a monotonic suffix for
uniqueness within the same second.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

_COUNTERS: dict[str, int] = {}
_LOCK = threading.Lock()


def _stamp(ts: datetime | None) -> str:
    ts = ts or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).strftime("%Y%m%d_%H%M%S")


def new_id(prefix: str, ts: datetime | None = None, seq: int | None = None) -> str:
    """Return a unique id of the form '<prefix>_<UTCstamp>_<seq>'."""
    if seq is not None:
        return f"{prefix}_{_stamp(ts)}_{seq:04d}"
    with _LOCK:
        key = f"{prefix}_{_stamp(ts)}"
        n = _COUNTERS.get(key, 0) + 1
        _COUNTERS[key] = n
        return f"{key}_{n:04d}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
