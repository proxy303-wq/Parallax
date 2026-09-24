"""The Dhan token regeneration guard.

Two failure modes, and they pull in opposite directions:

* generating while a healthy token is live INVALIDATES it, so a worker that
  re-mints on every call takes down a working session;
* refusing to re-mint forever is worse -- RenewToken only extends SELF tokens,
  so an APP token cannot be renewed and must be regenerated before it lapses.

The daily 08:00 refresh lands roughly 23h after generation, which is exactly
where a flat 23h guard refuses while the token is also about to die.
"""
from __future__ import annotations

import base64
import json
import time

import pytest

from parallax.adapters.broker import dhan_auth as A


def _jwt(seconds_left: float) -> str:
    """A token whose only meaningful field is exp (token_expiry reads it)."""
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": time.time() + seconds_left}).encode()).rstrip(b"=").decode()
    return "header." + payload + ".sig"


@pytest.fixture
def gen_probe(monkeypatch):
    """Count generations and stub out everything that touches disk/network."""
    calls = []
    monkeypatch.setattr(A, "_http_json",
                        lambda url, **kw: (calls.append(url), {"accessToken": "fresh"})[1])
    monkeypatch.setattr(A, "_mark_generation", lambda: None)
    monkeypatch.setattr(A, "save_token", lambda *a, **k: None)
    monkeypatch.setattr(A, "advisory", lambda: None, raising=False)
    return calls


def _run(monkeypatch, token, age_hours, gen_probe):
    monkeypatch.setattr(A, "load_saved_token", lambda *a, **k: token)
    monkeypatch.setattr(A, "hours_since_generation", lambda: age_hours)
    return A.generate_access_token("cid", "pin", "123456")


def test_regenerates_when_the_live_token_is_about_to_die(monkeypatch, gen_probe):
    """The daily 08:00 case: re-minted ~23h ago, ~1h of life left, APP type."""
    out = _run(monkeypatch, _jwt(3600), age_hours=23.2, gen_probe=gen_probe)
    assert out == "fresh", "must re-mint rather than let the session expire mid-morning"
    assert len(gen_probe) == 1


def test_declines_while_a_healthy_token_is_live(monkeypatch, gen_probe):
    """Regenerating here would invalidate a perfectly good session."""
    out = _run(monkeypatch, _jwt(20 * 3600), age_hours=2.0, gen_probe=gen_probe)
    assert out is None
    assert gen_probe == []


def test_generates_once_the_stamp_is_stale(monkeypatch, gen_probe):
    """No generation for 100h means the guard has lapsed.

    The guard is "not within 23h of the last generation", so a stale stamp
    permits a re-mint even though the current token looks healthy.  That case
    cannot arise on its own (a token with 10h left was minted 14h ago, not
    100h), which is exactly why it is worth pinning: it shows the freshness
    check is a real gate and not a no-op.
    """
    out = _run(monkeypatch, _jwt(10 * 3600), age_hours=100.0, gen_probe=gen_probe)
    assert out == "fresh"
    assert len(gen_probe) == 1


def test_generates_when_there_is_no_token_at_all(monkeypatch, gen_probe):
    out = _run(monkeypatch, None, age_hours=float("inf"), gen_probe=gen_probe)
    assert out == "fresh"
    assert len(gen_probe) == 1
