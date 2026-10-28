"""Gmail over the REST API, authorised by OAuth.

Why this exists alongside `imap_mail.py`: IMAP works anywhere and needs no
OAuth client, but it costs the user an app password - a long-lived, full-access
secret typed into a form, where a single wrong digit in the port produces a
ten-second TCP timeout and no useful diagnosis. This adapter takes a scoped,
revocable, auto-refreshing grant instead, and the user never types a secret.

Reading is done with `format=metadata` wherever possible. Gmail will happily
return the full MIME body of every message, and pulling message bodies the
system does not need, into a process that talks to a model, is the kind of
over-collection that is easy to avoid and awkward to explain later. Bodies are
fetched only for the handful of messages that actually score highly enough to
reach the user.
"""
from __future__ import annotations

import base64
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from ...core.oauth import access_token
from ..base import ActionResult, BaseConnector, FetchContext, RawSignal
from . import ConnectorAuthError, ConnectorError

log = logging.getLogger("duet.connectors.gmail")

API = "https://gmail.googleapis.com/gmail/v1/users/me"
TIMEOUT = 20.0
MAX_MESSAGES = 40
# Only this many get a body fetch - see the module docstring.
MAX_BODIES = 8


def _client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=API,
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT,
    )


def _header(payload: dict, name: str) -> str:
    for h in (payload.get("headers") or []):
        if h.get("name", "").lower() == name.lower():
            return str(h.get("value", ""))
    return ""


def _address(raw: str) -> str:
    """Pull the bare address out of `Display Name <a@b.com>`."""
    m = re.search(r"<([^>]+)>", raw)
    return (m.group(1) if m else raw).strip().lower()



# Reasons Google gives for a 403 that have nothing to do with the credential.
# Treating these as an auth failure would tell the user to reconnect an account
# that is perfectly fine, and the reconnection would fail the same way forever.
_NOT_A_CREDENTIAL_PROBLEM = frozenset({
    "accessNotConfigured",   # the API is not enabled on the Cloud project
    "SERVICE_DISABLED",      # the same thing, as it appears under error.details
    "rateLimitExceeded",
    "userRateLimitExceeded",
    "dailyLimitExceeded",
})


def _google_error(resp: httpx.Response) -> tuple[str, str]:
    """Pull Google's own message and machine-readable reason out of a failure.

    Worth the trouble: raising on the status alone produces "Client error '403
    Forbidden' for url ..." and discards the one sentence that says what to do
    about it - which for a disabled API is a link that fixes it in a click.
    """
    message = f"HTTP {resp.status_code}"
    reason = ""
    try:
        error = (resp.json() or {}).get("error") or {}
        message = str(error.get("message") or message)
        for item in error.get("errors") or []:
            if item.get("reason"):
                reason = str(item["reason"])
                break
        for detail in error.get("details") or []:
            if detail.get("reason"):
                reason = str(detail["reason"])
                break
    except Exception:
        # A non-JSON body (a proxy page, an HTML error). The status code still
        # classifies it well enough.
        pass
    return message, reason


def _check(resp: httpx.Response) -> None:
    """Raise the exception that describes what actually went wrong."""
    if resp.status_code < 400:
        return

    message, reason = _google_error(resp)

    if reason in _NOT_A_CREDENTIAL_PROBLEM:
        # Fixable, but not by touching the connection.
        raise ConnectorError(message)
    if resp.status_code in (401, 403):
        raise ConnectorAuthError(
            f"Google rejected the stored credential (HTTP {resp.status_code}): {message}"
        )
    raise ConnectorError(f"Gmail request failed (HTTP {resp.status_code}): {message}")


class GmailConnector(BaseConnector):
    key = "gmail"

    def fetch(self, ctx: FetchContext) -> tuple[list[RawSignal], dict[str, Any]]:
        if not ctx.credential:
            return [], ctx.cursor
        token = access_token(ctx.credential)

        cursor = dict(ctx.cursor)
        query = self._query(ctx, cursor)

        with _client(token) as http:
            listing = http.get(
                "/messages",
                params={"q": query, "maxResults": MAX_MESSAGES},
            )
            _check(listing)
            ids = [m["id"] for m in (listing.json().get("messages") or [])]
            if not ids:
                cursor["last_fetch"] = datetime.now(timezone.utc).isoformat()
                return [], cursor

            own = self._own_address(http)
            signals: list[RawSignal] = []

            for idx, mid in enumerate(ids):
                # Metadata for everything, bodies only for the first few.
                want_body = idx < MAX_BODIES
                params = (
                    {"format": "full"}
                    if want_body
                    else {
                        "format": "metadata",
                        "metadataHeaders": [
                            "From", "Subject", "Date", "List-Unsubscribe",
                            "List-Id", "Importance", "X-Priority", "To",
                        ],
                    }
                )
                r = http.get(f"/messages/{mid}", params=params)
                if r.status_code == 404:
                    continue  # deleted between list and fetch
                _check(r)
                signals.append(self._to_signal(r.json(), own, with_body=want_body))

        cursor["last_fetch"] = datetime.now(timezone.utc).isoformat()
        cursor["last_seen_id"] = ids[0] if ids else cursor.get("last_seen_id")
        return signals, cursor

    # ------------------------------------------------------------------ #
    def _query(self, ctx: FetchContext, cursor: dict) -> str:
        """Unread inbox first; widen to recent mail when the inbox is clean."""
        if ctx.since is not None:
            # Gmail's `after:` takes whole days, so this over-fetches slightly
            # and the unique constraint absorbs the overlap.
            return f"in:inbox after:{ctx.since.strftime('%Y/%m/%d')}"
        return "in:inbox is:unread"

    def _own_address(self, http: httpx.Client) -> str:
        try:
            r = http.get("/profile")
            _check(r)
            return str(r.json().get("emailAddress", "")).lower()
        except Exception:
            return ""

    def _to_signal(self, msg: dict, own: str, *, with_body: bool) -> RawSignal:
        payload = msg.get("payload") or {}
        sender_raw = _header(payload, "From")
        actor = _address(sender_raw)
        subject = _header(payload, "Subject") or "(no subject)"

        hints: dict[str, Any] = {}
        # A List-Unsubscribe header is the single most reliable "this is bulk"
        # signal there is, and the priority engine docks it hard.
        if _header(payload, "List-Unsubscribe") or _header(payload, "List-Id"):
            hints["bulk"] = True
        if _header(payload, "Importance").lower() == "high" or \
           _header(payload, "X-Priority").startswith("1"):
            hints["importance"] = "high"
        if own and actor and not actor.endswith(own.split("@")[-1]):
            hints["external"] = True
        if "UNREAD" in (msg.get("labelIds") or []):
            hints["unread"] = True
        if "IMPORTANT" in (msg.get("labelIds") or []):
            # Gmail's own importance classifier - worth something, not gospel.
            hints["importance"] = hints.get("importance") or "high"

        body = self._body(payload) if with_body else (msg.get("snippet") or "")

        occurred = datetime.now(timezone.utc)
        internal = msg.get("internalDate")
        if internal:
            try:
                occurred = datetime.fromtimestamp(int(internal) / 1000, tz=timezone.utc)
            except (ValueError, OSError):
                pass

        return RawSignal(
            external_id=f"gmail-{msg.get('id')}",
            kind="email",
            title=subject[:500],
            body=body[:4000],
            actor=actor,
            url=f"https://mail.google.com/mail/u/0/#inbox/{msg.get('id')}",
            occurred_at=occurred,
            raw={"thread_id": msg.get("threadId"), "labels": msg.get("labelIds")},
            hints=hints,
        )

    def _body(self, payload: dict) -> str:
        """First text/plain part, depth-first. HTML-only mail falls back to ''."""
        mime = payload.get("mimeType", "")
        if mime == "text/plain":
            data = (payload.get("body") or {}).get("data")
            if data:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace")
        for part in payload.get("parts") or []:
            found = self._body(part)
            if found:
                return found
        return ""

    # ------------------------------------------------------------------ #
    def execute(self, action: str, payload: dict[str, Any], ctx: FetchContext) -> ActionResult:
        if not ctx.credential:
            return ActionResult(ok=False, summary="gmail is not connected", error="no_credential")
        token = access_token(ctx.credential)

        if action in {"create_draft", "send_email"}:
            raw = self._mime(payload)
            with _client(token) as http:
                if action == "create_draft":
                    r = http.post("/drafts", json={"message": {"raw": raw}})
                    _check(r)
                    draft = r.json()
                    return ActionResult(
                        ok=True,
                        summary=f"Draft saved for {payload.get('to', 'recipient')}",
                        data={"draft_id": draft.get("id")},
                        external_id=draft.get("id"),
                        revert_plan={"action": "delete_draft", "id": draft.get("id")},
                    )
                r = http.post("/messages/send", json={"raw": raw})
                _check(r)
                sent = r.json()
                return ActionResult(
                    ok=True,
                    summary=f"Email sent to {payload.get('to', 'recipient')}",
                    data={"message_id": sent.get("id")},
                    external_id=sent.get("id"),
                )

        if action == "delete_draft":  # the revert path
            with _client(token) as http:
                http.delete(f"/drafts/{payload.get('id')}")
            return ActionResult(ok=True, summary="Draft deleted")

        return ActionResult(
            ok=False, summary=f"gmail.{action} is not implemented", error="not_implemented"
        )

    def _mime(self, payload: dict[str, Any]) -> str:
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["To"] = payload.get("to", "")
        msg["Subject"] = payload.get("subject", "")
        msg.set_content(payload.get("body", ""))
        return base64.urlsafe_b64encode(msg.as_bytes()).decode()
