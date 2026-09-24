"""daily_refresh must actually replace a token that is about to lapse.

RenewToken only extends SELF tokens.  An APP token therefore cannot be renewed
and has to be re-minted, which is the normal case on a fresh host that
bootstrapped itself from DHAN_PIN + DHAN_TOTP_SECRET.

The trap: refresh_token() returns early when the live token has more than
min_hours of life, so passing min_hours=0.0 makes a nearly-dead token count as
"still valid".  The refresh reports success, and the account dies minutes later
with the session still open.
"""
from __future__ import annotations

import base64
import json
import time

import pytest

from parallax.adapters.broker import dhan_auth as A


def _jwt(seconds_left: float) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": time.time() + seconds_left}).encode()).rstrip(b"=").decode()
    return "header." + payload + ".sig"


NEW_TOKEN = _jwt(24 * 3600)   # a real JWT: token_is_expired() parses exp


@pytest.fixture
def probe(monkeypatch):
    calls = []
    monkeypatch.setattr(A, "_http_json",
                        lambda url, **kw: (calls.append(url), {"accessToken": NEW_TOKEN})[1])
    monkeypatch.setattr(A, "_mark_generation", lambda: None)
    monkeypatch.setattr(A, "save_token", lambda *a, **k: None)
    # RenewToken is SELF-only: on an APP token it comes back empty.
    monkeypatch.setattr(A, "renew_token", lambda *a, **k: None)
    monkeypatch.setattr(A, "hours_since_generation", lambda: 24.0)
    monkeypatch.setattr(A, "load_saved_token", lambda *a, **k: _jwt(240))
    monkeypatch.setattr(A, "active_token", lambda: (_jwt(240), "saved file"))
    monkeypatch.setenv("PARALLAX_AUTO_GENERATE_TOKEN", "true")
    return calls


def test_daily_refresh_replaces_an_app_token_that_is_about_to_lapse(probe):
    """Four minutes of life left, not renewable -> a new token must be minted."""
    tok, src = A.daily_refresh("cid", "pin", "secret")
    assert tok == NEW_TOKEN, f"handed back a dying token ({src!r})"
    assert len(probe) == 1, "exactly one generation expected"


def test_daily_refresh_leaves_a_healthy_token_alone(probe, monkeypatch):
    """Regenerating here would invalidate a working session."""
    monkeypatch.setattr(A, "active_token", lambda: (_jwt(20 * 3600), "saved file"))
    tok, src = A.daily_refresh("cid", "pin", "secret")
    assert "still valid" in src
    assert probe == []
