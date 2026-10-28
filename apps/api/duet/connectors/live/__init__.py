"""Live connector adapters.

These talk to real vendor APIs over the network. Nothing here is scripted - if a
credential is missing the adapter returns nothing rather than inventing a
plausible-looking signal, because a fabricated signal in a briefing is worse
than a silent connector.

Three rules every adapter in this package follows:

1. **No credential, no work.** `fetch` returns `([], ctx.cursor)` and never
   raises. An unconfigured connection is a normal state, not an error, and the
   monitor sweeps every connection on a timer.
2. **Rejected credentials are loud.** A 401/403 raises `ConnectorAuthError` so
   `monitor._sweep_connection` can mark the connection errored with a message a
   human can act on. Silently returning nothing would look like "inbox quiet".
3. **Throttling is not failure.** A 429 means we asked for too much, not that
   anything is wrong. Adapters return the signals gathered so far and leave the
   cursor where it was, so the next sweep retries the same window.

The secret itself is resolved through `vault.inject`, which runs a callback with
the plaintext and returns only the callback's result. HTTP adapters pull the one
token they need; the IMAP adapter runs its whole session inside the callback so
the password never exists outside it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..base import FetchContext

# Every outbound call is bounded. A vendor hanging must not wedge the sweep for
# the other connectors queued behind it.
TIMEOUT = 20.0


class ConnectorError(RuntimeError):
    """The vendor said no for a reason the user may be able to fix."""


class ConnectorAuthError(ConnectorError):
    """The credential was rejected. Surfaces in the UI as "reconnect this app"."""


def resolve_secret(ctx: FetchContext, *names: str) -> str | None:
    """Return the first of `names` present in the vaulted credential.

    Adapters accept several field names for the same thing ("token", "api_key")
    because the UI that captured the credential and the vendor that issues it
    rarely agree on what to call it.
    """
    if not ctx.credential:
        return None

    from ...core.vault import get_vault

    def pick(credential: dict[str, Any]) -> str | None:
        for name in names:
            value = credential.get(name)
            if value:
                return str(value)
        return None

    return get_vault().inject(ctx.credential, pick)


def is_auth_status(status_code: int) -> bool:
    return status_code in (401, 403)


def raise_for_auth(connector: str, status_code: int, body: str = "") -> None:
    """Turn an HTTP auth rejection into the one exception the monitor reports."""
    if is_auth_status(status_code):
        detail = f": {body[:200]}" if body else ""
        raise ConnectorAuthError(
            f"{connector} rejected the stored credential (HTTP {status_code}){detail}"
        )


def parse_iso(value: Any) -> datetime | None:
    """Parse the ISO8601 shapes vendors actually emit, including a `Z` suffix."""
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_epoch(value: Any) -> datetime | None:
    """Unix seconds -> aware datetime. Stripe and Slack both date things this way."""
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "TIMEOUT",
    "ConnectorAuthError",
    "ConnectorError",
    "is_auth_status",
    "now_iso",
    "parse_epoch",
    "parse_iso",
    "raise_for_auth",
    "resolve_secret",
]
