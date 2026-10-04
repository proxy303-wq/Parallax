"""Historical MCX commodity prices (futures + options) for backtesting.

Dhan serves daily OHLC for both the FUTCOM future and its OPTFUT options via
/charts/historical - confirmed live on the CRUDEOIL front month.  The same
security-id path used for the live chain works for history, so a collar can be
replayed over real futures AND real option premiums, not a synthetic surface.
"""
from __future__ import annotations

import datetime
import json
import urllib.error
import urllib.request

HIST_URL = "https://api.dhan.co/v2/charts/historical"
SEGMENT = "MCX_COMM"


def fetch_historical(security_id, instrument: str, from_date: str, to_date: str,
                     token: str, client_id: str) -> dict:
    """Daily OHLC for one MCX contract.  instrument is FUTCOM or OPTFUT."""
    body = json.dumps({
        "securityId": str(security_id), "exchangeSegment": SEGMENT,
        "instrument": instrument, "expiryCode": 0, "oi": False,
        "fromDate": from_date, "toDate": to_date,
    }).encode()
    req = urllib.request.Request(HIST_URL, data=body,
                                 headers={"access-token": token, "client-id": client_id,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def series(data: dict) -> dict[str, float]:
    """{date: close} from a historical response, in IST dates."""
    out: dict[str, float] = {}
    ts = data.get("timestamp") or []
    cl = data.get("close") or []
    for t, c in zip(ts, cl):
        d = datetime.datetime.utcfromtimestamp(t + 19800).date().isoformat()
        out[d] = float(c)
    return out
