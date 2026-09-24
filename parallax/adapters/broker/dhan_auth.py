"""Dhan token resolution — validate -> RenewToken -> TOTP (dependency-free).

Ports the verified proxy.dhan_auth flow into PARALLAX.  The 24-hour access
token expires; this module renews it automatically using:

    1. DHAN_ACCESS_TOKEN (env) if still valid
    2. a saved token file if still valid
    3. GET /v2/RenewToken  (+24h, SELF tokens only)
    4. TOTP (RFC 6238) from DHAN_PIN + DHAN_TOTP_SECRET (APP token)

Secrets are never logged.  Only ONE process may generate tokens: every TOTP
generateAccessToken INVALIDATES the previous token, so two generators do not
race, they take turns destroying each other's session.

That rule used to be enforced by a local stamp file, which holds on one box and
collapses on a PaaS -- six containers each get their own filesystem, each sees
"never generated", and each fires a generation.  The token and the generation
stamp therefore live in the shared database whenever one is configured (see
parallax/adapters/db.py), and generation itself is serialised with a database
advisory lock.  Set PARALLAX_AUTO_GENERATE_TOKEN=false to opt a worker out
entirely.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

AUTH_BASE = "https://auth.dhan.co"
API_BASE = "https://api.dhan.co/v2"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
DEFAULT_TOKEN_FILE = os.environ.get("DHAN_TOKEN_FILE") or os.path.join(
    _REPO_ROOT, ".dhan_token.txt")

#: Keys in the shared key/value store (parallax/adapters/db.py).  When a
#: database is configured these replace the per-process files outright.
_TOKEN_KEY = "dhan_access_token"
_GEN_KEY = "dhan_token_generated_at"

_B32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def _db():
    """The shared-state module, imported lazily so startup stays cheap."""
    from parallax.adapters import db as _dbmod
    return _dbmod


def _shared_state() -> bool:
    """True when the token lives in a database shared by every worker."""
    try:
        return _db().is_postgres()
    except Exception:
        return False


def _http_json(url, method="GET", headers=None, params=None, data=None, timeout=20):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method=method, headers=headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, body, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"status": "error", "http": exc.code,
                "text": exc.read().decode(errors="replace")[:300]}


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def token_expiry(token: str) -> float:
    try:
        payload = token.split(".")[1]
        return float(json.loads(_b64url_decode(payload)).get("exp", 0))
    except Exception:
        return 0.0


def token_is_expired(token: str, margin_s: int = 3600) -> bool:
    exp = token_expiry(token)
    return exp <= 0 or exp - time.time() < margin_s


def _b32decode(s: str) -> bytes:
    s = (s or "").upper().strip().replace(" ", "").replace("-", "")
    bits = 0
    value = 0
    out = bytearray()
    for ch in s:
        idx = _B32_ALPHABET.find(ch)
        if idx < 0:
            continue
        value = (value << 5) | idx
        bits += 5
        if bits >= 8:
            bits -= 8
            out.append((value >> bits) & 0xFF)
    return bytes(out)


def totp(secret: str, for_time: float | None = None, digits: int = 6,
         period: int = 30) -> str:
    if for_time is None:
        for_time = time.time()
    counter = int(for_time // period)
    key = _b32decode(secret)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def renew_token(client_id: str, token: str) -> str | None:
    data = _http_json(f"{API_BASE}/RenewToken", method="GET",
                      headers={"access-token": token, "dhanClientId": client_id})
    return data.get("accessToken", "") or None


_GEN_STAMP = os.path.join(_REPO_ROOT, ".dhan_token_generated")

#: A token is "due for renewal" below this remaining life.  Dhan tokens last
#: exactly 24h from generation, so a daily 08:00 refresh always lands on a
#: token with minutes left - it must renew on a margin, never on expiry.
MIN_LIFE_H = 6.0


def hours_since_generation() -> float:
    """Hours since the last TOTP generation (inf if never).

    Read from the shared store when there is one.  A per-container file stamp
    guards nothing: six containers each read "never generated" and fire six
    generations, five of which kill the others' tokens.
    """
    if _shared_state():
        try:
            v = _db().state_get(_GEN_KEY)
        except Exception:
            # Cannot tell when the last generation happened, so assume "just
            # now" and refuse to generate.  A spurious generation invalidates a
            # token another worker may be trading on; skipping one only costs a
            # retry.
            return 0.0
        return (time.time() - float(v)) / 3600.0 if v else float("inf")
    try:
        if os.path.exists(_GEN_STAMP):
            with open(_GEN_STAMP, encoding="utf-8") as fh:
                return (time.time() - float(fh.read().strip())) / 3600.0
    except (OSError, ValueError):
        pass
    return float("inf")


def _mark_generation() -> None:
    if _shared_state():
        try:
            _db().state_set(_GEN_KEY, str(time.time()))
        except Exception:
            pass
    try:
        with open(_GEN_STAMP, "w", encoding="utf-8") as fh:
            fh.write(str(time.time()))
    except OSError:
        pass


def generate_access_token(client_id: str, pin: str, totp_code: str,
                          min_interval_hours: float = 23.0) -> str | None:
    """Generate a fresh TOTP access token.

    GUARDED to at most one generation per 'min_interval_hours' (default 23h -
    one hour under the 24h token life, so a daily fixed-time refresh at 10:00
    always passes rather than being blocked by a few seconds' drift).  Every generation INVALIDATES the previous
    token, so re-generating while a usable one is still inside its window can
    only break a working session.  RenewToken (which extends an existing token
    without creating a new one) is NOT rate-limited by this guard and is always
    tried first."""
    with _db().advisory_lock():
        # Re-check INSIDE the lock.  The guard is only a guard if the stamp it
        # reads is shared AND the read-then-write is atomic; without the lock
        # two workers both read "never generated" and both fire.
        age = hours_since_generation()
        if age < min_interval_hours:
            return None
        data = _http_json(f"{AUTH_BASE}/app/generateAccessToken", method="POST",
                          params={"dhanClientId": client_id, "pin": pin, "totp": totp_code})
        tok = data.get("accessToken", "") or None
        if tok:
            _mark_generation()
            # Publish it BEFORE dropping the lock.  Otherwise the next worker
            # waiting outside wakes to age==0, correctly declines to generate,
            # then looks for a shared token that has not been written yet and
            # concludes there is none -- so five of six containers still fail to
            # authenticate even though the lock did its job.
            save_token(tok)
        return tok




# ------------------------------------------------------------
# token health + proactive refresh
# ------------------------------------------------------------

def active_token() -> tuple[str, str]:
    """The token resolve_token() would actually use, and where it lives.

    DHAN_ACCESS_TOKEN in the environment and the saved token file disagree the
    moment any refresh has happened: the env var is a stale leftover and the
    file holds the live one.  Picking env blindly made token_status() report an
    expired token while the system was working fine, and made _ensure_token()
    fire a refresh on every call - and a TOTP refresh INVALIDATES the working
    token, so that path can only ever break a live session.
    """
    from parallax.adapters.env import env as _env
    etok = _env("DHAN_ACCESS_TOKEN") or ""
    if etok and not token_is_expired(etok, margin_s=0):
        return etok, "env"
    ftok = load_saved_token() or ""
    if ftok and not token_is_expired(ftok, margin_s=0):
        return ftok, "saved file"
    for name, tok in (("env (expired)", etok), ("saved file (expired)", ftok)):
        if tok:
            return tok, name
    return "", "none"


def token_status(token: str | None = None) -> dict:
    """Report the access token's type and remaining life (health check)."""
    from parallax.adapters.env import env as _env
    src = "explicit"
    if token:
        tok = token
    else:
        tok, src = active_token()
    exp = token_expiry(tok)
    hours = round((exp - time.time()) / 3600, 2) if exp else -1.0
    ttype = ""
    try:
        payload = tok.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        ttype = json.loads(base64.urlsafe_b64decode(payload)).get("tokenConsumerType", "")
    except Exception:
        pass
    return {"type": ttype, "hours_left": hours, "expires_at": exp,
            "valid": hours > 0, "source": src, "token_file": DEFAULT_TOKEN_FILE}


def refresh_token(client_id: str, pin: str = "", totp_secret: str = "",
                  min_hours: float = 12.0, notify=print) -> tuple:
    """Proactively refresh the token before it lapses.

    Order: RenewToken (keeps the SELF type, which market data needs) then TOTP
    (fresh APP token - trading APIs work, market data may not).  Every TOTP
    generation invalidates the previous token, so only call this when needed.
    Returns (token, source)."""
    tok, _src = active_token()
    if tok and not token_is_expired(tok, margin_s=int(min_hours * 3600)):
        return tok, "still valid (" + _src + ")"
    if tok:
        renewed = renew_token(client_id, tok)
        if renewed and not token_is_expired(renewed, margin_s=0):
            save_token(renewed)
            notify("token refreshed via RenewToken")
            return renewed, "renewed via RenewToken"
    if pin and totp_secret:
        if os.environ.get("PARALLAX_AUTO_GENERATE_TOKEN", "true").lower() == "false":
            return None, "auto-generation disabled"
        age = hours_since_generation()
        new = generate_access_token(client_id, pin, totp(totp_secret))
        if new and not token_is_expired(new, margin_s=0):
            save_token(new)
            notify("token regenerated via TOTP")
            return new, "regenerated via TOTP"
        fresh = load_saved_token()
        if fresh and not token_is_expired(fresh, margin_s=0):
            return fresh, "shared token (generated by another worker)"
        if new is None and age < 23.0:
            return None, (f"generation skipped: last was {age:.1f}h ago "
                          f"(limit is one per day)")
    return None, "refresh failed (no usable token)"


def daily_refresh(client_id: str, pin: str = "", totp_secret: str = "",
                  notify=print) -> tuple:
    """Explicit daily refresh, meant to run at 08:00 before the session.

    Tries RenewToken first - it extends an ACTIVE token by 24h and keeps the
    token type (SELF stays SELF, which market data needs).  Falls back to the
    normal chain (saved -> TOTP).  Returns (token, source).

    RENEWS ON A MARGIN, not on expiry.  A token issued at 08:00 lives exactly
    24h, so at 08:00 the next day it has minutes left - "valid" by the
    margin_s=0 test, which meant this function did nothing, the runner marked
    the day done, and the account was dead five minutes later.  Anything under
    MIN_LIFE_H is treated as due.
    """
    tok, src = active_token()
    left_h = (token_expiry(tok) - time.time()) / 3600.0 if tok else -1.0
    if tok and left_h > MIN_LIFE_H:
        return tok, "still valid (%.1fh, %s)" % (left_h, src)
    if tok and left_h > 0:
        renewed = renew_token(client_id, tok)
        if renewed and not token_is_expired(renewed, margin_s=0):
            save_token(renewed)
            notify("token renewed via RenewToken (+24h), had %.1fh left" % left_h)
            return renewed, "RenewToken +24h"
    return refresh_token(client_id, pin, totp_secret, min_hours=0.0, notify=notify)


def load_saved_token(path: str = DEFAULT_TOKEN_FILE) -> str | None:
    """The current token, from the shared store when there is one.

    Falls back to the file so a single-box install and the test suite behave
    exactly as they did before.
    """
    if _shared_state():
        try:
            tok = (_db().state_get(_TOKEN_KEY) or "").strip()
            if tok:
                return tok
        except Exception:
            pass
    try:
        if os.path.exists(path):
            tok = open(path, encoding="utf-8-sig").read().strip()
            if tok:
                return tok
    except OSError:
        pass
    return None


def save_token(token: str, path: str = DEFAULT_TOKEN_FILE) -> None:
    """Persist the token to every available store, best-effort.

    A container filesystem can be read-only, and failing to write a local mirror
    must never take down the process that just obtained a perfectly good token.
    """
    if _shared_state():
        try:
            _db().state_set(_TOKEN_KEY, token)
        except Exception:
            pass
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(token)
    except OSError:
        pass


def resolve_token(client_id: str, access_token: str = "", pin: str = "",
                  totp_secret: str = "", token_file: str = DEFAULT_TOKEN_FILE,
                  notify=print) -> tuple[str | None, str]:
    """Best-effort token resolution.  Returns (token, source) or (None, reason)."""
    candidates: list[tuple[str, str]] = []
    if access_token:
        candidates.append(("env token", access_token))
    saved = load_saved_token(token_file)
    if saved:
        candidates.append(("saved token", saved))

    for name, tok in candidates:
        if tok and not token_is_expired(tok):
            return tok, name

    for name, tok in candidates:
        if not tok:
            continue
        renewed = renew_token(client_id, tok)
        if renewed and not token_is_expired(renewed, margin_s=0):
            save_token(renewed, token_file)
            notify("dhan token renewed via RenewToken (+24h)")
            return renewed, f"renewed ({name})"

    if pin and totp_secret:
        if os.environ.get("PARALLAX_AUTO_GENERATE_TOKEN", "true").lower() == "false":
            return None, "token expired and auto-generation disabled"
        code = totp(totp_secret)
        tok = generate_access_token(client_id, pin, code)
        if tok and not token_is_expired(tok, margin_s=0):
            save_token(tok, token_file)
            notify("dhan token auto-generated via TOTP (funds/portfolio only)")
            return tok, "TOTP auto-generated"
        # A "failure" here usually means another worker won the generation lock
        # a moment ago.  Every generation invalidates the previous token, so the
        # right move is to adopt the winner's token, never to retry.
        fresh = load_saved_token(token_file)
        if fresh and not token_is_expired(fresh, margin_s=0):
            return fresh, "shared token (generated by another worker)"
        return None, "TOTP token generation failed"

    return None, "no usable token and no DHAN_PIN/DHAN_TOTP_SECRET configured"

# ------------------------------------------------------------
# SELF-token consent flow (one-time browser login -> market data)
# ------------------------------------------------------------
# APP tokens (TOTP-generated) can trade (orders/funds) but CANNOT read market
# data (option chain / quotes).  A SELF token needs a one-time browser consent:
#   1. api_key_consent()      -> consentAppId
#   2. user opens consent_login_url() and logs in, pastes the redirect tokenId
#   3. consume_consent()      -> accessToken (SELF)

API_KEY_FILE = os.environ.get(
    "DHAN_API_KEY_FILE",
    r"C:\Athena_X\dhan API KKEY.txt" if os.name == "nt" else "")


def _value_after_label(line, label):
    rest = line[len(label):].strip()
    for sep in (":", "-", "="):
        if rest.startswith(sep):
            return rest[len(sep):].strip()
    return rest


def load_api_keypair(path=API_KEY_FILE):
    if not os.path.exists(path):
        return None, None
    key = secret = None
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            low = line.lower()
            if low.startswith("api key"):
                key = _value_after_label(line, "API KEY") or _value_after_label(line, "api key")
            elif low.startswith("api secret"):
                secret = _value_after_label(line, "API Secret") or _value_after_label(line, "api secret")
    except OSError:
        return None, None
    return (key or None), (secret or None)


def api_key_consent(api_key, secret, client_id=None):
    params = {"client_id": client_id} if client_id else {}
    return _http_json(f"{AUTH_BASE}/app/generate-consent", method="POST",
                      params=params, headers={"app_id": api_key, "app_secret": secret})


def consent_login_url(consent_id):
    return f"{AUTH_BASE}/login/consentApp-login?consentAppId={urllib.parse.quote(consent_id)}"


def consume_consent(api_key, secret, token_id):
    return _http_json(f"{AUTH_BASE}/app/consumeApp-consent", method="POST",
                      params={"tokenId": token_id},
                      headers={"app_id": api_key, "app_secret": secret})


def start_self_token_consent(client_id=None, notify=print):
    """Begin the SELF-token flow.  Returns the login URL (or None)."""
    key, secret = load_api_keypair()
    if not (key and secret):
        notify("Dhan API key/secret file not found")
        return None
    consent = api_key_consent(key, secret, client_id=client_id)
    cid = consent.get("consentAppId") or consent.get("consentId") or consent.get("consent_id") or ""
    if not cid:
        notify(f"consent failed: {consent}")
        return None
    return consent_login_url(cid)


def finish_self_token_consent(token_id, notify=print):
    """Exchange a pasted tokenId (or redirect URL) for a SELF access token."""
    key, secret = load_api_keypair()
    if not (key and secret):
        notify("Dhan API key/secret file not found")
        return None
    if "tokenId=" in str(token_id):
        token_id = str(token_id).split("tokenId=")[-1].split("&")[0]
    data = consume_consent(key, secret, str(token_id).strip())
    tok = data.get("accessToken", "")
    if not tok:
        notify(f"consume-consent failed: {data}")
        return None
    save_token(tok, DEFAULT_TOKEN_FILE)
    return tok

