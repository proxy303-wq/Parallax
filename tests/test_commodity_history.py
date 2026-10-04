import datetime
from parallax.adapters.market_data.commodity_history import series

def test_series_maps_timestamps_to_ist_dates():
    d = {"timestamp": [1759248000], "close": [9008.0]}
    out = series(d)
    # 1759248000 is 2026-09-30 16:00 UTC = 2026-09-30 21:30 IST
    assert list(out.values()) == [9008.0]


def test_series_handles_empty():
    assert series({}) == {}
    assert series({"timestamp": [], "close": []}) == {}


def test_series_skips_missing_close():
    d = {"timestamp": [1759248000], "close": []}
    assert series(d) == {}
