"""The two fixes from the 2026-09-29 session.

The condors entered at 09:42 instead of 09:30 and both still won, so nothing
would ever have surfaced either fault:

  * the token in .env was REVOKED (a newer one had been minted to the file) but
    still unexpired, so it passed every local check and 401'd every call
  * with the entry 12 minutes late, the ATM was struck 100 points lower on
    BANKNIFTY than the 09:30 slot would have given - the difference between
    breaching the call side intraday and missing it by 3 points
"""
import base64
import datetime
import json

import pytest

from parallax.adapters.broker import dhan_auth as A

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def jwt(iat, exp, cid="1100"):
    def seg(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return "%s.%s.sig" % (seg({"alg": "HS512"}), seg(
        {"iat": iat, "exp": exp, "dhanClientId": cid}))


OLD = jwt(1790000000, 1791000000)
NEW = jwt(1790600000, 1791000000)


def test_issued_at_reads_the_jwt():
    assert A.issued_at(NEW) == 1790600000
    assert A.issued_at("not-a-jwt") == 0.0
    assert A.issued_at("") == 0.0


def _env_is(monkeypatch, token):
    """active_token() reads through parallax.adapters.env.env, not os.environ."""
    monkeypatch.setattr("parallax.adapters.env.env", lambda *a, **k: token)


def test_the_newer_token_wins_even_when_both_are_unexpired(monkeypatch):
    """The whole bug: the revoked older token used to win on 'unexpired'."""
    monkeypatch.setattr(A, "load_saved_token", lambda *a, **k: NEW)
    monkeypatch.setattr(A, "token_is_expired", lambda t, margin_s=0: False)
    _env_is(monkeypatch, OLD)
    tok, src = A.active_token()
    assert tok == NEW and src == "saved file"


def test_the_newer_env_token_wins_the_other_way(monkeypatch):
    monkeypatch.setattr(A, "load_saved_token", lambda *a, **k: OLD)
    monkeypatch.setattr(A, "token_is_expired", lambda t, margin_s=0: False)
    _env_is(monkeypatch, NEW)
    tok, src = A.active_token()
    assert tok == NEW and src == "env"


def test_an_expired_token_is_never_preferred(monkeypatch):
    monkeypatch.setattr(A, "load_saved_token", lambda *a, **k: NEW)
    monkeypatch.setattr(A, "token_is_expired", lambda t, margin_s=0: t != NEW)
    _env_is(monkeypatch, OLD)
    tok, _ = A.active_token()
    assert tok == NEW


def test_no_token_at_all(monkeypatch):
    monkeypatch.setattr(A, "load_saved_token", lambda *a, **k: None)
    _env_is(monkeypatch, "")
    assert A.active_token() == ("", "none")


def test_token_works_is_false_when_dhan_rejects(monkeypatch):
    """_http_json RETURNS the error rather than raising, so the check has to
    read the body.  Testing only for an exception reported a dead token as
    healthy - the exact bug this function exists to prevent."""
    monkeypatch.setattr(A, "_http_json", lambda *a, **k: {
        "status": "error", "http": 400,
        "text": '{"errorType":"Order_Error","errorCode":"DH-906",'
                '"errorMessage":"Invalid Token"}'})
    assert A.token_works("x", retries=0) is False


def test_token_works_is_false_on_a_401_eight_oh_eight(monkeypatch):
    monkeypatch.setattr(A, "_http_json", lambda *a, **k: {
        "status": "error", "http": 401,
        "text": '{"data":{"808":"Authentication Failed - Client ID or Token invalid"}}'})
    assert A.token_works("x", retries=0) is False


def test_token_works_still_false_if_it_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(A, "_http_json", boom)
    assert A.token_works("x", retries=0) is False


def test_token_works_is_true_on_a_clean_call(monkeypatch):
    monkeypatch.setattr(A, "_http_json", lambda *a, **k: {"data": {}})
    assert A.token_works("x") is True


def test_daily_refresh_remints_a_revoked_but_unexpired_token(monkeypatch):
    """The 08:00 gate.  'Still valid' must mean the API agrees."""
    monkeypatch.setattr(A, "active_token", lambda: (NEW, "env"))
    monkeypatch.setattr(A, "token_expiry", lambda t: __import__("time").time() + 22 * 3600)
    monkeypatch.setattr(A, "token_works", lambda *a, **k: False)
    seen = []
    monkeypatch.setattr(A, "refresh_token",
                        lambda *a, **k: (seen.append(1), ("fresh", "TOTP"))[1])
    tok, src = A.daily_refresh("1100", notify=lambda m: None)
    assert seen, "a rejected token must fall through to a re-mint"
    assert tok == "fresh"
