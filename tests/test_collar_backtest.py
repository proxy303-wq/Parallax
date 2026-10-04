from dataclasses import dataclass
from parallax.apps.research.collar_backtest import CollarSpec, backtest_expiry, stats

class _Resp:
    def __init__(self, obj): self._o = obj
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self):
        import json; return json.dumps(self._o).encode()

def _series(dates, closes):
    import datetime
    utc = datetime.timezone.utc
    ts = [int(datetime.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=utc).timestamp()) - 19800
          for d in dates]
    return {"timestamp": ts, "close": closes}

def test_backtest_expiry_replays_a_collar(monkeypatch):
    import parallax.apps.research.collar_backtest as cb
    fut = {"2026-09-01": 9000.0, "2026-09-14": 9000.0, "2026-09-15": 8800.0}
    put = {"2026-09-01": 200.0, "2026-09-14": 200.0, "2026-09-15": 700.0}
    call = {"2026-09-01": 200.0, "2026-09-14": 200.0, "2026-09-15": 0.0}
    grid = {8550.0: {"PE": 1, "CE": 2}, 9500.0: {"PE": 3, "CE": 4}}
    monkeypatch.setattr(cb, "contracts", lambda s, e: (grid, 99))
    def fake_hist(sid, inst, lo, hi, token, cid):
        if inst == "FUTCOM": return _series(list(fut), list(fut.values()))
        return _series(list(put), list(put.values()))
    monkeypatch.setattr(cb, "fetch_historical", fake_hist)
    spec = CollarSpec(put_pct=5.0, call_pct=5.0, entry_days_before_expiry=1)
    t = backtest_expiry("CRUDEOIL", "2026-09-15", spec, "tok", "cid")
    # entry 2026-09-14: F0 9000, put 8550 @200, call 9500 @200, net 0
    # settle 8800: pnl = (8800-9000) + max(8550-8800,0) - max(8800-9500,0) - 0 = -200
    assert t["entry"] == "2026-09-14"
    assert t["put_strike"] == 8550.0 and t["call_strike"] == 9500.0
    assert t["pnl_points"] == -200.0
    assert t["pnl_rs"] == -200.0 * 100  # crude lot 100


def test_backtest_returns_none_when_data_too_short(monkeypatch):
    import parallax.apps.research.collar_backtest as cb
    monkeypatch.setattr(cb, "contracts", lambda s, e: ({9000.0: {"PE": 1, "CE": 2}}, 99))
    monkeypatch.setattr(cb, "fetch_historical",
                        lambda *a, **k: {"timestamp": [1759248000], "close": [9000.0]})
    assert backtest_expiry("CRUDEOIL", "2026-09-15", CollarSpec(), "tok", "cid") is None


def test_stats_aggregates():
    tr = [{"pnl_points": 10.0, "pnl_rs": 1000.0},
          {"pnl_points": -5.0, "pnl_rs": -500.0},
          {"pnl_points": 20.0, "pnl_rs": 2000.0}]
    s = stats(tr)
    assert s["n"] == 3 and s["wins"] == 2 and s["losses"] == 1
    assert s["total_points"] == 25.0 and s["avg_points"] == round(25.0 / 3, 2)
    assert s["worst"] == -5.0 and s["best"] == 20.0
    assert s["total_rs"] == 2500.0


def test_stats_empty():
    assert stats([]) == {"n": 0}
