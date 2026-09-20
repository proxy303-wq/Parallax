"""Verify the /crypto route renders one card per symbol and reads the per-symbol keys."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.environ.get("PARALLAX_ROOT", r"C:\Parallax"))
os.environ["PARALLAX_DB"] = os.path.join(tempfile.mkdtemp(prefix="dash_"), "j.db")

from fastapi.testclient import TestClient          # noqa: E402
from parallax.web.store import JournalStore        # noqa: E402

store = JournalStore()
store.set_setting("crypto_state_BTCUSD", json.dumps({
    "symbol": "BTCUSD", "interval": "1h", "mode": "paper", "bias": 1, "equity": 800000,
    "last_price": 80293.0, "last_bar": "2026-09-20 06:00:00+00:00", "bars_processed": 898,
    "position": None, "order": None, "zone": None,
    "events": ["armed limit 79000.0 stop 77500.0 dir=1"]}))
store.set_setting("crypto_state_ETHUSD", json.dumps({
    "symbol": "ETHUSD", "interval": "1h", "mode": "paper", "bias": -1, "equity": 800000,
    "last_price": 2573.2, "last_bar": "2026-09-20 06:00:00+00:00", "bars_processed": 898,
    "position": {"side": -1, "entry": 2600.0, "stop": 2680.0, "qty": 68, "risk": 5800.0},
    "order": None, "zone": None, "events": []}))

from parallax.web.app import app                  # noqa: E402

c = TestClient(app)
r = c.get("/crypto")
print("HTTP", r.status_code)
html = r.text
checks = ["FVG retest, BTCUSD 1h", "FVG retest, ETHUSD 1h", "SMC XAUTUSD",
          "80,293", "2,573", "Open position", "Worker has not run yet"]
for probe in checks:
    print("  %-22s %s" % (probe, "present" if probe in html else "-"))
print("  %-22s %s" % ("ETH shows SHORT", "SHORT" in html))
print("  %-22s %s" % ("BTC bias LONG", "LONG" in html))
print("  %-22s %s" % ("contract stat shown", "Contract" in html))
# a card must be labelled for its OWN symbol
print("  %-22s %d" % ("ETH title count", html.count("FVG retest, ETHUSD 1h")))
print("  %-22s %d" % ("BTC title count", html.count("FVG retest, BTCUSD 1h")))
print("  %-22s %s" % ("no stray BTC/USD label", "BTC/USD" not in html))
# the legacy key must still drive the BTC card when the scoped key is absent
store.set_setting("crypto_state_BTCUSD", "")
store.set_setting("crypto_state", json.dumps({"symbol": "BTCUSD", "last_price": 11111.0,
                                              "bias": 0, "equity": 1, "events": []}))
r2 = c.get("/crypto")
print("  %-22s %s" % ("legacy fallback works", "11,111" in r2.text))
