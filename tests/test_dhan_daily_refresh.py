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
    """Regenerating here would invalidate a working session.

    'Healthy' now means the API said so, not merely that the JWT has hours left,
    so token_works() is stubbed rather than left to make a real call.
    """
    monkeypatch.setattr(A, "active_token", lambda: (_jwt(20 * 3600), "saved file"))
    monkeypatch.setattr(A, "token_works", lambda *a, **k: True)
    tok, src = A.daily_refresh("cid", "pin", "secret")
    assert "still valid" in src
    assert probe == []


def test_daily_refresh_remints_a_token_the_api_rejects(probe, monkeypatch):
    """2026-09-29, exactly.  22 hours of life, correct client id, and every call
    answered 401/808 because a newer mint had revoked it.  The session started
    on that token and both condors missed the 09:30 entry."""
    monkeypatch.setattr(A, "active_token", lambda: (_jwt(22 * 3600), "env"))
    monkeypatch.setattr(A, "token_works", lambda *a, **k: False)
    tok, src = A.daily_refresh("cid", "pin", "secret", notify=lambda m: None)
    assert tok == NEW_TOKEN, "a token the API rejects must not be handed back"
    assert len(probe) == 1


def test_a_revoked_token_is_reminted_even_inside_the_generation_guard(probe, monkeypatch):
    """2026-10-02.  Four workers each ran the 08:00 refresh; every mint REVOKES
    the previous token, so the one left on disk was dead while still parsing
    with ~23h of life.

    hours_since_generation() was minutes, so the once-a-day guard held,
    generation was declined, and the account stayed dead for the whole
    session with no way back - the guard was protecting a token Dhan had
    already thrown away.  force is what makes rejection, not local expiry,
    the deciding vote.
    """
    monkeypatch.setattr(A, "hours_since_generation", lambda: 0.6)
    monkeypatch.setattr(A, "active_token", lambda: (_jwt(22.9 * 3600), "saved file"))
    monkeypatch.setattr(A, "token_works", lambda *a, **k: False)
    tok, src = A.daily_refresh("cid", "pin", "secret", notify=lambda m: None)
    assert tok == NEW_TOKEN, "the guard must not protect a token Dhan rejects"
    assert len(probe) == 1


def test_the_guard_still_holds_for_a_token_that_works(probe, monkeypatch):
    """The guard exists so a second worker cannot revoke a live session."""
    monkeypatch.setattr(A, "hours_since_generation", lambda: 0.6)
    monkeypatch.setattr(A, "active_token", lambda: (_jwt(22.9 * 3600), "saved file"))
    monkeypatch.setattr(A, "token_works", lambda *a, **k: True)
    tok, src = A.daily_refresh("cid", "pin", "secret", notify=lambda m: None)
    assert "still valid" in src
    assert probe == []
