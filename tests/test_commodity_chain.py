import os
from parallax.config.commodities import spec
from parallax.adapters.market_data import commodity_chain as cc

def test_spec_resolves():
    assert spec("GOLDM").step == 500.0
    assert spec("CRUDEOIL").step == 50.0


def test_nearest_expiry_picks_first_after_today(monkeypatch):
    monkeypatch.setattr(cc, "list_expiries", lambda s: ["2026-10-15", "2026-11-17"])
    assert cc.nearest_expiry("CRUDEOIL", today="2026-10-20") == "2026-11-17"
    assert cc.nearest_expiry("CRUDEOIL", today="2026-10-01") == "2026-10-15"


def test_nearest_expiry_falls_back_to_last_when_all_past(monkeypatch):
    monkeypatch.setattr(cc, "list_expiries", lambda s: ["2026-10-15"])
    assert cc.nearest_expiry("CRUDEOIL", today="2027-01-01") == "2026-10-15"


def test_nearest_expiry_none_when_no_expiries(monkeypatch):
    monkeypatch.setattr(cc, "list_expiries", lambda s: [])
    assert cc.nearest_expiry("CRUDEOIL") is None


def test_fetch_ltp_batches(monkeypatch):
    calls = []
    def fake(req, timeout=None):
        import json
        seg = json.loads(req.data)["MCX_COMM"]
        calls.append(len(seg))
        return _Resp({"data": {"MCX_COMM": {str(i): {"last_price": 100.0} for i in seg}},
                      "status": "success"})
    class _Resp:
        def __init__(self, obj): self._obj = obj
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            import json; return json.dumps(self._obj).encode()
    monkeypatch.setattr(cc, "BATCH", 3)
    monkeypatch.setattr(cc.urllib.request, "urlopen", fake)
    out = cc.fetch_ltp([1, 2, 3, 4, 5, 6, 7], "tok", "cid")
    assert len(out) == 7
    assert calls == [3, 3, 1]
