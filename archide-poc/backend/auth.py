"""OIDC authentication (Ф12.1).

The platform issues every tenant a Keycloak client (realm `tenants`, client
`<project>-web`), so ArchIDE must be able to require a login. Two modes:

* ``auth.mode = "none"`` — the local/standalone version: the server is open (as
  before) and the caller passes ``user`` explicitly;
* ``auth.mode = "oidc"`` — the platform version: ``/api/*`` needs a session,
  obtained through the authorization-code flow.

Only the standard library is available, so the ID token is **not** verified
locally (no RSA in stdlib). Instead the code is exchanged at the token endpoint
and the identity is read from the provider's **UserInfo** endpoint, which the
provider itself validates — the standard code-flow + userinfo pattern.

The session is a signed cookie (HMAC-SHA256), not a server-side store, so the pod
stays stateless and survives restarts.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

DISCOVERY_TTL = 600
_discovery_cache: dict[str, tuple[float, dict]] = {}

SESSION_COOKIE = "archide_session"
STATE_COOKIE = "archide_state"
SESSION_TTL = 12 * 3600


def settings(cfg: dict) -> dict:
    """The `auth` section with environment overrides (the image is configured by env)."""
    auth = dict((cfg or {}).get("auth") or {})
    env = {
        "mode": os.environ.get("ARCHIDE_AUTH"),
        "issuer": os.environ.get("ARCHIDE_OIDC_ISSUER"),
        "client_id": os.environ.get("ARCHIDE_OIDC_CLIENT_ID"),
        "client_secret": os.environ.get("ARCHIDE_OIDC_CLIENT_SECRET"),
        "session_secret": os.environ.get("ARCHIDE_SESSION_SECRET"),
        "redirect_uri": os.environ.get("ARCHIDE_OIDC_REDIRECT_URI"),
        "scopes": os.environ.get("ARCHIDE_OIDC_SCOPES"),
    }
    for key, value in env.items():
        if value:
            auth[key] = value
    auth.setdefault("mode", "none")
    auth.setdefault("scopes", "openid profile email")
    return auth


def enabled(cfg: dict) -> bool:
    auth = settings(cfg)
    return str(auth.get("mode", "none")).lower() == "oidc" and bool(auth.get("issuer"))


def discovery(issuer: str) -> dict:
    """Provider metadata, cached for a few minutes."""
    issuer = str(issuer).rstrip("/")
    cached = _discovery_cache.get(issuer)
    now = time.time()
    if cached and now - cached[0] < DISCOVERY_TTL:
        return cached[1]
    url = issuer + "/.well-known/openid-configuration"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=10) as resp:
        data = json.loads(resp.read() or b"{}")
    if not isinstance(data, dict) or not data.get("authorization_endpoint"):
        raise ValueError(f"invalid discovery document at {url}")
    _discovery_cache[issuer] = (now, data)
    return data


def _form_post(url: str, data: dict, timeout: float = 15) -> dict:
    body = urllib.parse.urlencode(data).encode()
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    request.add_header("Accept", "application/json")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:300]
        raise ValueError(f"token endpoint rejected the code: {exc.code} {detail}") from exc


def _get_json(url: str, token: str, timeout: float = 15) -> dict:
    request = urllib.request.Request(url, method="GET")
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("Accept", "application/json")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def login_url(cfg: dict, redirect_uri: str, state: str) -> str:
    auth = settings(cfg)
    meta = discovery(str(auth["issuer"]))
    query = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": auth.get("client_id", ""),
        "redirect_uri": redirect_uri,
        "scope": auth.get("scopes", "openid profile email"),
        "state": state,
    })
    return f"{meta['authorization_endpoint']}?{query}"


def exchange(cfg: dict, code: str, redirect_uri: str) -> dict:
    """Trade the code for tokens and read the identity from userinfo."""
    auth = settings(cfg)
    meta = discovery(str(auth["issuer"]))
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": auth.get("client_id", ""),
    }
    if auth.get("client_secret"):
        payload["client_secret"] = auth["client_secret"]
    tokens = _form_post(meta["token_endpoint"], payload)
    access = tokens.get("access_token")
    if not access:
        raise ValueError("the provider returned no access token")
    userinfo = meta.get("userinfo_endpoint")
    claims = _get_json(userinfo, access) if userinfo else {}
    return {"tokens": tokens, "claims": claims}


def identity(claims: dict) -> str:
    """The app's user id: prefer the login, then the e-mail, then the subject."""
    for key in ("preferred_username", "email", "sub"):
        value = str((claims or {}).get(key) or "").strip()
        if value:
            return value
    return ""


# --- session cookie (signed, stateless) -------------------------------------

def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session(cfg: dict, user: str) -> str:
    secret = str(settings(cfg).get("session_secret") or "")
    if not secret:
        raise ValueError("auth.session_secret is required for oidc mode")
    payload = base64.urlsafe_b64encode(json.dumps(
        {"u": user, "t": int(time.time())}).encode()).decode().rstrip("=")
    return f"{payload}.{_sign(secret, payload)}"


def session_user(cfg: dict, cookie: str) -> str:
    """The user behind a session cookie, or "" when it is missing/invalid/expired."""
    secret = str(settings(cfg).get("session_secret") or "")
    if not cookie or not secret or "." not in cookie:
        return ""
    payload, _, signature = cookie.rpartition(".")
    if not hmac.compare_digest(_sign(secret, payload), signature):
        return ""
    try:
        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except (ValueError, json.JSONDecodeError):
        return ""
    if int(data.get("t") or 0) + SESSION_TTL < time.time():
        return ""
    return str(data.get("u") or "")


def new_state() -> str:
    return secrets.token_urlsafe(24)


def cookie(name: str, value: str, max_age: int, secure: bool) -> str:
    parts = [f"{name}={value}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={max_age}"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)
