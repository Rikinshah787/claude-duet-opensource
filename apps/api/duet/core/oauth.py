"""OAuth 2.0 authorization-code flow with PKCE.

What this replaces: app passwords. An app password is a long-lived secret with
full mailbox access that the user types into a form, and that is both fragile
(one typo and you get a ten-second TCP timeout with no useful error) and
over-privileged. OAuth gives a scoped, revocable, refreshable grant that the
user approves in their provider's own UI, and the access token this app holds
expires in an hour.

Design notes that matter for security:

* **PKCE on every flow**, not just public clients. The code verifier never
  leaves this process, so an attacker who intercepts the redirect still cannot
  exchange the code.
* **`state` is a single-use CSRF nonce** held in memory with a short TTL. A
  callback whose state we did not issue is rejected outright - that is the
  check that stops someone walking you into connecting *their* account.
* **Tokens go straight to the vault.** They are never returned to a caller,
  never logged, and never reach a model prompt. The refresh happens inside
  `access_token()`, so adapters ask for a token and get a fresh one without
  ever seeing the refresh token.
* **Client secrets are configuration, not user input.** They come from env, so
  they are not in the database and not in anything a user can paste.

The user still has to create an OAuth client with the provider once. That is
unavoidable - it is their project, under their account, with their consent
screen - but it is a five-minute, one-time step and then connecting is a click.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from urllib.parse import urlencode

from ..config import settings
from .vault import get_vault

log = logging.getLogger("duet.oauth")

# How long an authorization attempt stays valid. Short on purpose: this is a
# window during which a replayed callback would be accepted.
STATE_TTL_S = 600
REFRESH_SKEW_S = 120  # refresh this long before actual expiry


@dataclass(frozen=True)
class OAuthProvider:
    key: str                      # provider key, e.g. "google"
    label: str
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    client_id_env: str
    client_secret_env: str
    # Providers differ on how to get a refresh token at all.
    extra_authorize_params: dict[str, str] = field(default_factory=dict)
    revoke_url: str | None = None
    setup_url: str = ""
    setup_hint: str = ""


PROVIDERS: dict[str, OAuthProvider] = {
    "google": OAuthProvider(
        key="google",
        label="Google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=(
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.compose",
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/calendar.events",
            "openid",
            "email",
        ),
        client_id_env="DUET_GOOGLE_CLIENT_ID",
        client_secret_env="DUET_GOOGLE_CLIENT_SECRET",
        # Google only returns a refresh token on the FIRST consent unless you
        # ask for offline access and force the prompt. Without these two, the
        # connection silently dies an hour later.
        extra_authorize_params={"access_type": "offline", "prompt": "consent"},
        revoke_url="https://oauth2.googleapis.com/revoke",
        setup_url="https://console.cloud.google.com/apis/credentials",
        setup_hint=(
            "Create an OAuth client (type: Web application) and add "
            "http://127.0.0.1:8787/api/oauth/callback as an authorised redirect URI."
        ),
    ),
    "slack": OAuthProvider(
        key="slack",
        label="Slack",
        authorize_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
        scopes=("channels:history", "groups:history", "im:history", "users:read", "chat:write"),
        client_id_env="DUET_SLACK_CLIENT_ID",
        client_secret_env="DUET_SLACK_CLIENT_SECRET",
        setup_url="https://api.slack.com/apps",
        setup_hint="Create an app, add the scopes, and set the redirect URL.",
    ),
    "github": OAuthProvider(
        key="github",
        label="GitHub",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        scopes=("repo", "notifications", "read:user"),
        client_id_env="DUET_GITHUB_CLIENT_ID",
        client_secret_env="DUET_GITHUB_CLIENT_SECRET",
        setup_url="https://github.com/settings/developers",
        setup_hint="Create an OAuth App and set the callback URL.",
    ),
}

# Which connector each provider's grant satisfies. One Google consent covers
# Gmail and Calendar, which is the whole point of doing this per-provider
# rather than per-app.
PROVIDER_FOR_CONNECTOR: dict[str, str] = {
    "gmail": "google",
    "google_calendar": "google",
    "google_drive": "google",
    "google_sheets": "google",
    "slack": "slack",
    # Deliberately NOT github: the GitHub adapter shells out to the `gh` CLI
    # and reuses the sign-in already on the machine, so it never looks at a
    # stored credential. Advertising a sign-in button here would bank a token
    # that nothing reads and report success for a connection that changed
    # nothing. The provider stays defined above, ready for a token-based
    # adapter.
}


# Connectors whose adapter can use *nothing but* an OAuth grant. The Gmail
# adapter calls `access_token()`, which rejects any record that is not an
# oauth2 grant - so offering a paste-a-token form for these would bank a
# credential that fails on first use. Slack is not here: its adapter reads a
# plain `access_token`/`bot_token` field, so a pasted bot token works just as
# well as a completed sign-in.
OAUTH_ONLY: frozenset[str] = frozenset({
    "gmail", "google_calendar", "google_drive", "google_sheets",
})


def provider_for(connector_key: str) -> OAuthProvider | None:
    return PROVIDERS.get(PROVIDER_FOR_CONNECTOR.get(connector_key, ""))


def _client_credential(env_name: str) -> str:
    """Resolve one OAuth client credential by its environment-variable name.

    The provider table is keyed by env var *name* so the setup hint can tell a
    user exactly what to set. But values must be read through `settings`, not
    `os.environ`: pydantic-settings loads `.env` into the settings object and
    never writes it back to the process environment, so a credential put in
    `.env` is invisible to `os.environ` and the flow reports itself
    unconfigured while looking correctly set up on disk.

    The `os.environ` fallback keeps a plain exported variable working too,
    which is how this runs in a container.
    """
    attr = env_name.removeprefix("DUET_").lower()
    value = getattr(settings, attr, "") or os.environ.get(env_name, "")
    return str(value).strip()


def is_configured(provider: OAuthProvider) -> bool:
    """True when this deployment has credentials for the provider."""
    return bool(_client_credential(provider.client_id_env) and
                _client_credential(provider.client_secret_env))


def redirect_uri() -> str:
    # Must match what is registered with the provider, byte for byte. Google in
    # particular rejects 'localhost' vs '127.0.0.1' mismatches.
    base = settings.oauth_redirect_base or f"http://{settings.host}:{settings.port}"
    return f"{base}/api/oauth/callback"


# --------------------------------------------------------------------------- #
# Pending authorizations
# --------------------------------------------------------------------------- #
@dataclass
class _Pending:
    provider_key: str
    connector_key: str
    user_id: str
    verifier: str
    created_at: float


# In memory, not the database: these live for seconds, and a restart should
# invalidate every half-finished authorization rather than resume it.
_pending: dict[str, _Pending] = {}


def _sweep_expired() -> None:
    now = time.time()
    for state, row in list(_pending.items()):
        if now - row.created_at > STATE_TTL_S:
            del _pending[state]


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return verifier, challenge


def start(connector_key: str, user_id: str) -> dict[str, Any]:
    """Build the URL to send the user to. Returns the URL and the state nonce."""
    provider = provider_for(connector_key)
    if provider is None:
        raise ValueError(f"{connector_key} does not use OAuth")
    if not is_configured(provider):
        raise RuntimeError(
            f"{provider.label} OAuth is not set up on this machine. "
            f"Create a client at {provider.setup_url}, then set "
            f"{provider.client_id_env} and {provider.client_secret_env}. "
            f"{provider.setup_hint}"
        )

    _sweep_expired()
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)
    _pending[state] = _Pending(
        provider_key=provider.key,
        connector_key=connector_key,
        user_id=user_id,
        verifier=verifier,
        created_at=time.time(),
    )

    params = {
        "client_id": _client_credential(provider.client_id_env),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(provider.scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **provider.extra_authorize_params,
    }
    query = urlencode(params)
    return {
        "url": f"{provider.authorize_url}?{query}",
        "state": state,
        "provider": provider.key,
        "expires_in": STATE_TTL_S,
    }


def finish(state: str, code: str) -> dict[str, Any]:
    """Exchange the code for tokens and store them. Returns a summary only."""
    _sweep_expired()
    pending = _pending.pop(state, None)
    if pending is None:
        # Either forged, replayed, or simply too slow. All three are refusals.
        raise PermissionError("That sign-in link is no longer valid. Start again.")

    provider = PROVIDERS[pending.provider_key]
    payload = {
        "client_id": _client_credential(provider.client_id_env),
        "client_secret": _client_credential(provider.client_secret_env),
        "code": code,
        "code_verifier": pending.verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri(),
    }
    r = httpx.post(
        provider.token_url,
        data=payload,
        headers={"Accept": "application/json"},
        timeout=30.0,
    )
    r.raise_for_status()
    token = r.json()

    if token.get("error"):
        raise RuntimeError(f"{provider.label} refused the exchange: {token.get('error')}")

    _store(pending.user_id, pending.connector_key, provider, token)
    return {
        "connector_key": pending.connector_key,
        "provider": provider.key,
        "scopes": token.get("scope", " ".join(provider.scopes)).split(),
        "has_refresh": bool(token.get("refresh_token")),
    }


def _store(user_id: str, connector_key: str, provider: OAuthProvider, token: dict) -> None:
    # Absent `expires_in` means the grant does not expire on a clock - Slack
    # bot tokens and GitHub OAuth tokens are both like this. Defaulting to an
    # hour here would have invented an expiry and then refused to use a
    # perfectly good token, because there is no refresh token to renew it with.
    raw_expiry = token.get("expires_in")
    expires_at = time.time() + int(raw_expiry) if raw_expiry else None
    record = {
        "type": "oauth2",
        "provider": provider.key,
        "user_id": user_id,
        "connector_key": connector_key,
        "access_token": token.get("access_token") or token.get("authed_user", {}).get("access_token"),
        "refresh_token": token.get("refresh_token"),
        "expires_at": expires_at,
        "scope": token.get("scope", ""),
    }
    get_vault().put(user_id, connector_key, record)
    log.info("stored oauth grant for %s/%s", provider.key, connector_key)


# --------------------------------------------------------------------------- #
# Using the grant
# --------------------------------------------------------------------------- #
def access_token(handle) -> str:
    """Return a usable access token, refreshing it first if it is near expiry.

    Adapters call this and never see the refresh token. The refreshed value is
    written back to the vault so the next call is cheap.
    """
    vault = get_vault()
    record = vault.inject(handle, lambda c: dict(c))

    if record.get("type") != "oauth2":
        raise ValueError("this credential is not an OAuth grant")

    expires_at = record.get("expires_at")
    if expires_at is None:
        # No clock expiry. It stays valid until the user revokes it, at which
        # point the API call fails with a 401 and the sweep records that - the
        # honest outcome, rather than a refresh we cannot perform.
        return str(record["access_token"])
    if float(expires_at) - REFRESH_SKEW_S > time.time():
        return str(record["access_token"])

    refresh = record.get("refresh_token")
    if not refresh:
        raise PermissionError(
            "This connection expired and has no refresh token. Reconnect the app."
        )

    provider = PROVIDERS[record["provider"]]
    r = httpx.post(
        provider.token_url,
        data={
            "client_id": _client_credential(provider.client_id_env),
            "client_secret": _client_credential(provider.client_secret_env),
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        },
        headers={"Accept": "application/json"},
        timeout=30.0,
    )
    r.raise_for_status()
    fresh = r.json()
    if fresh.get("error"):
        raise PermissionError(f"Refresh refused: {fresh['error']}. Reconnect the app.")

    record["access_token"] = fresh["access_token"]
    renewed = fresh.get("expires_in")
    record["expires_at"] = time.time() + int(renewed) if renewed else None
    # Providers usually omit refresh_token on refresh; keep the one we have.
    if fresh.get("refresh_token"):
        record["refresh_token"] = fresh["refresh_token"]

    # The record carries its own owner, so the write-back needs no lookup.
    vault.put(str(record["user_id"]), str(record["connector_key"]), record)
    return str(record["access_token"])


def status(connector_key: str) -> dict[str, Any]:
    """What the UI needs to decide between an OAuth button and a token form."""
    provider = provider_for(connector_key)
    if provider is None:
        return {"oauth": False}
    return {
        "oauth": True,
        "provider": provider.key,
        "label": provider.label,
        "configured": is_configured(provider),
        # True when signing in is the only way to connect this app, so the UI
        # must not also offer a credential form.
        "required": connector_key in OAUTH_ONLY,
        "setup_url": provider.setup_url,
        "setup_hint": provider.setup_hint,
        "redirect_uri": redirect_uri(),
        "client_id_env": provider.client_id_env,
        "client_secret_env": provider.client_secret_env,
        "scopes": list(provider.scopes),
    }
