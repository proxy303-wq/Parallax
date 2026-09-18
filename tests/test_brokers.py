"""Broker adapter tests — offline (no network): signing, contract resolution,
dry_run order safety."""
import hashlib
import hmac

from parallax.contracts import (
    OrderIntent, OrderStatus, OrderType, Side, ValidatedOrderIntent,
)
from parallax.adapters.broker.delta import DeltaBroker
from parallax.adapters.broker.dhan import DhanBroker


def test_delta_signing_is_deterministic_hmac():
    b = DeltaBroker(api_key="key", api_secret="secret", dry_run=True)
    sig = b._sign("123", "GET", "/v2/positions", "")
    expected = hmac.new(b"secret", b"GET123/v2/positions",
                        hashlib.sha256).hexdigest()
    assert sig == expected
    # METHOD comes first, timestamp is part of the signed message
    assert b._sign("123", "GET", "/x", "") != b._sign("123", "POST", "/x", "")


def test_delta_dry_run_order_is_safe():
    b = DeltaBroker(api_key="key", api_secret="secret", dry_run=True)
    i = OrderIntent(intent_id="t", decision_id="d", risk_auth_id="r",
                    instrument="DELTA:BTC", side=Side.BUY, quantity=0.1,
                    order_type=OrderType.MARKET, idempotency_key="k")
    ack = b.place_order(ValidatedOrderIntent(i, True))
    assert ack.status == OrderStatus.NEW
    assert ack.order_id == "DRY"
    assert "NOT sent" in ack.message


def test_dhan_resolves_nifty_futures_contract():
    b = DhanBroker(dry_run=True, connect_on_init=False)
    sid, sym, expiry, lot = b.resolve_contract()
    assert sid > 0
    assert sym.upper().startswith("NIFTY-")
    assert lot == 65.0
    assert expiry


def test_dhan_dry_run_converts_lots_to_units():
    b = DhanBroker(dry_run=True, connect_on_init=False)
    i = OrderIntent(intent_id="t", decision_id="d", risk_auth_id="r",
                    instrument="NSE:NIFTY", side=Side.BUY, quantity=5.0,
                    order_type=OrderType.MARKET, idempotency_key="k")
    ack = b.place_order(ValidatedOrderIntent(i, True))
    assert ack.status == OrderStatus.NEW
    assert "325 units" in ack.message      # 5 lots x 65 units/lot
    assert "NOT sent" in ack.message
