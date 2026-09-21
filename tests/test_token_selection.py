"""Token selection must follow resolve_token(), not the environment blindly.

DHAN_ACCESS_TOKEN in the environment and the saved token file disagree as soon
as any refresh has happened.  Picking the env var made token_status() report an
expired token while the system worked fine, and made _ensure_token() attempt a
refresh on every call - and a TOTP refresh invalidates the working token, so
that path can only break a live session.
"""
import base64
import json
import time

import parallax.adapters.broker.dhan_auth as A
import parallax.adapters.env as env_mod


def _jwt(exp: float, kind: str = "APP") -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(exp), "tokenConsumerType": kind}).encode()
    ).decode().rstrip("=")
    return "header." + payload + ".sig"


def _wire(monkeypatch, env_token, file_token):
    monkeypatch.setattr(env_mod, "env",
                        lambda name, default="": env_token if name == "DHAN_ACCESS_TOKEN" else default)
    monkeypatch.setattr(A, "load_saved_token", lambda *a, **k: file_token)


def test_valid_file_wins_over_a_stale_env(monkeypatch):
    _wire(monkeypatch, _jwt(time.time() - 12 * 3600), _jwt(time.time() + 22 * 3600))
    assert A.active_token()[1] == "saved file"
    st = A.token_status()
    assert st["valid"] is True
    assert st["source"] == "saved file"
    assert st["hours_left"] > 20


def test_valid_env_wins_when_the_file_is_stale(monkeypatch):
    _wire(monkeypatch, _jwt(time.time() + 20 * 3600), _jwt(time.time() - 5 * 3600))
    assert A.active_token()[1] == "env"
    assert A.token_status()["valid"] is True


def test_both_expired_reports_invalid_and_says_which(monkeypatch):
    _wire(monkeypatch, _jwt(time.time() - 12 * 3600), _jwt(time.time() - 3 * 3600))
    assert A.active_token()[1] == "env (expired)"   # env is tried first
    assert A.token_status()["valid"] is False


def test_no_tokens_at_all(monkeypatch):
    _wire(monkeypatch, "", None)
    tok, src = A.active_token()
    assert tok == "" and src == "none"


def test_refresh_token_is_a_noop_while_the_file_token_is_healthy(monkeypatch):
    """The important one: no TOTP regeneration that would kill a live token."""
    _wire(monkeypatch, _jwt(time.time() - 12 * 3600), _jwt(time.time() + 22 * 3600))
    called = []
    monkeypatch.setattr(A, "renew_token", lambda *a: called.append("renew"))
    monkeypatch.setattr(A, "generate_access_token",
                        lambda *a, **k: called.append("totp"))
    tok, src = A.refresh_token("cid", "pin", "secret", min_hours=12.0)
    assert src.startswith("still valid")
    assert called == []


def test_refresh_token_renews_the_valid_file_token_not_the_dead_env_one(monkeypatch):
    _wire(monkeypatch, _jwt(time.time() - 12 * 3600), _jwt(time.time() + 2 * 3600))
    seen = []

    def fake_renew(cid, tok):
        seen.append(tok)
        return _jwt(time.time() + 26 * 3600)

    monkeypatch.setattr(A, "renew_token", fake_renew)
    monkeypatch.setattr(A, "save_token", lambda *a, **k: None)
    tok, src = A.refresh_token("cid", "pin", "secret", min_hours=12.0)
    assert len(seen) == 1
    # the token handed to RenewToken must be the live file token, not the dead env one
    assert A.token_expiry(seen[0]) > time.time() + 3600
    assert "RenewToken" in src
