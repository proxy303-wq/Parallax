"""Dhan token resolution — validate -> RenewToken -> TOTP (dependency-free).

Ports the verified proxy.dhan_auth flow into PARALLAX.  The 24-hour access
token expires; this module renews it automatically using:

    1. DHAN_ACCESS_TOKEN (env) if still valid
    2. a saved token file if still valid
    3. GET /v2/RenewToken  (+24h, SELF tokens only)
    4. TOTP (RFC 6238) from DHAN_PIN + DHAN_TOTP_SECRET (APP token)

Secrets are never logged.  Only ONE process should generate tokens (every TOTP
generateAccessToken invalidates the previous token) — set
PARALLAX_AUTO_GENERATE_TOKEN=false if another system is the generator.
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

_B32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


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


def generate_access_token(client_id: str, pin: str, totp_code: str) -> str | None:
    data = _http_json(f"{AUTH_BASE}/app/generateAccessToken", method="POST",
                      params={"dhanClientId": client_id, "pin": pin, "totp": totp_code})
    return data.get("accessToken", "") or None




# ------------------------------------------------------------
# token health + proactive refresh
# ------------------------------------------------------------

def token_status(token: str | None = None) -> dict:
    """Report the access token's type and remaining life (health check)."""
    tok = token or os.environ.get("DHAN_ACCESS_TOKEN") or load_saved_token() or ""
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
            "valid": hours > 0, "token_file": DEFAULT_TOKEN_FILE}


def refresh_token(client_id: str, pin: str = "", totp_secret: str = "",
                  min_hours: float = 12.0, notify=print) -> tuple:
    """Proactively refresh the token before it lapses.

    Order: RenewToken (keeps the SELF type, which market data needs) then TOTP
    (fresh APP token - trading APIs work, market data may not).  Every TOTP
    generation invalidates the previous token, so only call this when needed.
    Returns (token, source)."""
    tok = os.environ.get("DHAN_ACCESS_TOKEN") or load_saved_token() or ""
    if tok and not token_is_expired(tok, margin_s=int(min_hours * 3600)):
        return tok, "still valid"
    if tok:
        renewed = renew_token(client_id, tok)
        if renewed and not token_is_expired(renewed, margin_s=0):
            save_token(renewed)
            notify("token refreshed via RenewToken")
            return renewed, "renewed via RenewToken"
    if pin and totp_secret:
        if os.environ.get("PARALLAX_AUTO_GENERATE_TOKEN", "true").lower() == "false":
            return None, "auto-generation disabled"
        new = generate_access_token(client_id, pin, totp(totp_secret))
        if new and not token_is_expired(new, margin_s=0):
            save_token(new)
            notify("token regenerated via TOTP")
            return new, "regenerated via TOTP"
    return None, "refresh failed (no usable token)"


def load_saved_token(path: str = DEFAULT_TOKEN_FILE) -> str | None:
    try:
        if os.path.exists(path):
            tok = open(path, encoding="utf-8-sig").read().strip()
            if tok:
                return tok
    except OSError:
        pass
    return None


def save_token(token: str, path: str = DEFAULT_TOKEN_FILE) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(token)


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

