"""Generic IMAP mail.

Real email without an OAuth app. A Gmail app password, Fastmail, a company
Dovecot box - anything that speaks IMAP works, which makes this the one mail
connector a user can switch on in under a minute.

Two decisions worth stating:

**The mailbox is opened read-only.** `SELECT` would let the server mark every
message we fetch as `\\Seen`, so a proactive sweep would quietly read the user's
unread mail out from under them. `EXAMINE` (readonly=True) does not.

**The password never leaves `vault.inject`.** Unlike the HTTP adapters, which
pull a bearer token out and send it in a header, the whole IMAP session runs
inside the injection callback. The password exists only on that frame, is never
returned, never logged, and never placed in a signal, a cursor or an exception
message.
"""
from __future__ import annotations

import email
import imaplib
import logging
from datetime import datetime, timezone
from email.header import decode_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from ..base import BaseConnector, FetchContext, RawSignal
from . import ConnectorAuthError, ConnectorError, now_iso

log = logging.getLogger("duet.connectors.imap")

DEFAULT_HOST = "imap.gmail.com"
DEFAULT_PORT = 993
DEFAULT_MAILBOX = "INBOX"

# One sweep's ceiling. A neglected inbox can hold thousands of unread messages;
# the newest thirty are the ones a proactive assistant has anything to say about.
MESSAGE_LIMIT = 30
BODY_CHARS = 4000

# Headers that mark machine-sent mail. Anything carrying List-Unsubscribe was
# sent to a list, not to the user, and `noise_factor` subtracts heavily for it.
BULK_HEADERS = ("list-unsubscribe", "list-id", "list-post")


class ImapConnector(BaseConnector):
    key = "imap"

    # ------------------------------------------------------------------ #
    # Read
    # ------------------------------------------------------------------ #
    def fetch(self, ctx: FetchContext) -> tuple[list[RawSignal], dict[str, Any]]:
        if not ctx.credential:
            return [], ctx.cursor

        from ...core.vault import get_vault

        # The entire session - connect, login, search, fetch, parse - happens
        # inside this callback, so nothing the vault resolved outlives it.
        def run(credential: dict[str, Any]) -> tuple[list[RawSignal], dict[str, Any]]:
            return self._sweep(credential, ctx)

        return get_vault().inject(ctx.credential, run)

    def _sweep(
        self, credential: dict[str, Any], ctx: FetchContext
    ) -> tuple[list[RawSignal], dict[str, Any]]:
        host = str(credential.get("host") or DEFAULT_HOST)
        port = int(credential.get("port") or DEFAULT_PORT)
        username = str(credential.get("username") or credential.get("user") or "")
        password = str(credential.get("password") or credential.get("app_password") or "")
        mailbox = str(credential.get("mailbox") or DEFAULT_MAILBOX)

        if not username or not password:
            return [], ctx.cursor

        cursor = dict(ctx.cursor)
        last_uid = int(cursor.get("last_uid") or 0)

        try:
            conn = imaplib.IMAP4_SSL(host, port)
        except OSError as exc:
            raise ConnectorError(f"Could not reach IMAP host {host}:{port}: {exc}") from exc

        try:
            try:
                conn.login(username, password)
            except imaplib.IMAP4.error as exc:
                # Deliberately reports the account and host only. The server's
                # own message is safe, but nothing from `credential` is added.
                raise ConnectorAuthError(
                    f"IMAP login rejected for {username} at {host}: {exc}"
                ) from exc

            # readonly: see the module docstring - a sweep must not consume the
            # unread flag it is reporting on.
            status, _ = conn.select(mailbox, readonly=True)
            if status != "OK":
                raise ConnectorError(f"IMAP mailbox {mailbox!r} could not be opened")

            uids = self._search(conn, ctx)
            fresh = [uid for uid in uids if uid > last_uid][-MESSAGE_LIMIT:]

            signals: list[RawSignal] = []
            own_domain = username.split("@")[-1].lower() if "@" in username else ""
            for uid in fresh:
                message = self._fetch_message(conn, uid)
                if message is None:
                    continue
                signals.append(self._to_signal(message, uid, mailbox, own_domain))
                last_uid = max(last_uid, uid)
        finally:
            # Both are best effort: a half-closed socket is not worth failing a
            # sweep that already collected its signals.
            try:
                conn.close()
            except (imaplib.IMAP4.error, OSError):
                pass
            try:
                conn.logout()
            except (imaplib.IMAP4.error, OSError):
                pass

        cursor["last_uid"] = last_uid
        cursor["last_fetch"] = now_iso()
        return signals, cursor

    def _search(self, conn: Any, ctx: FetchContext) -> list[int]:
        """UID search. UIDs are stable across sessions; sequence numbers are not."""
        if ctx.since is not None:
            # IMAP SINCE takes a date, not a timestamp, and is inclusive of the
            # whole day - the UID watermark does the precise filtering.
            criteria = ("SINCE", ctx.since.strftime("%d-%b-%Y"))
        else:
            criteria = ("UNSEEN",)

        status, data = conn.uid("SEARCH", None, *criteria)
        if status != "OK" or not data:
            return []
        raw = data[0]
        if isinstance(raw, bytes):
            raw = raw.decode("ascii", errors="ignore")
        return sorted(int(part) for part in str(raw).split() if part.isdigit())

    def _fetch_message(self, conn: Any, uid: int) -> Message | None:
        status, data = conn.uid("FETCH", str(uid), "(RFC822)")
        if status != "OK" or not data:
            return None
        for part in data:
            # imaplib hands back a list mixing tuples and bare bytes separators.
            if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], bytes):
                return email.message_from_bytes(part[1])
        return None

    # ------------------------------------------------------------------ #
    def _to_signal(
        self, message: Message, uid: int, mailbox: str, own_domain: str
    ) -> RawSignal:
        subject = _decode(message.get("Subject")) or "(no subject)"
        from_name, from_addr = parseaddr(_decode(message.get("From")))
        body = _text_body(message)

        hints: dict[str, Any] = {}
        if any(message.get(header) for header in BULK_HEADERS):
            hints["bulk"] = True
        if _is_high_priority(message):
            hints["importance"] = "high"
        sender_domain = from_addr.split("@")[-1].lower() if "@" in from_addr else ""
        if sender_domain and own_domain and sender_domain != own_domain:
            # Only meaningful as a contrast with the user's own domain; flagging
            # every message external would make the hint worthless.
            hints["external"] = True

        # Message-ID is the globally stable identity; the UID is only unique
        # within this mailbox, so it is the fallback rather than the default.
        message_id = (_decode(message.get("Message-ID")) or "").strip("<> ")
        external_id = message_id or f"{mailbox}:uid:{uid}"

        return RawSignal(
            external_id=external_id,
            kind="email",
            title=subject,
            body=body,
            actor=from_addr or from_name,
            url=None,  # IMAP has no web address for a message
            occurred_at=_sent_at(message),
            raw={
                "uid": uid,
                "mailbox": mailbox,
                "from_name": from_name,
                "to": _decode(message.get("To")),
                "message_id": message_id,
            },
            hints=hints,
        )

    # `execute` is intentionally not implemented: IMAP is a read protocol, and
    # sending mail would mean SMTP and a second credential. BaseConnector's
    # default refuses every write with `not_implemented`.


# --------------------------------------------------------------------------- #
# Header and body decoding
# --------------------------------------------------------------------------- #
def _decode(value: str | None) -> str:
    """Decode an RFC2047 header into real text.

    `=?UTF-8?B?...?=` is extremely common in real subject lines, and handing the
    encoded form to the briefing is mojibake the user sees. `decode_header`
    returns a list of (bytes-or-str, charset) runs because one header can mix
    several encodings.
    """
    if not value:
        return ""
    parts: list[str] = []
    try:
        runs = decode_header(value)
    except (UnicodeDecodeError, ValueError):
        return str(value)
    for text, charset in runs:
        if isinstance(text, bytes):
            parts.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(text)
    return "".join(parts).strip()


def _text_body(message: Message) -> str:
    """First text/plain part that is not an attachment, truncated."""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() != "text/plain":
                continue
            disposition = str(part.get("Content-Disposition") or "")
            if "attachment" in disposition.lower():
                continue
            text = _payload_text(part)
            if text:
                return text[:BODY_CHARS]
        return ""
    if message.get_content_type() == "text/plain":
        return _payload_text(message)[:BODY_CHARS]
    return ""


def _payload_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace").strip()
    except LookupError:
        # An unknown charset name is common in spam; latin-1 never fails.
        return payload.decode("latin-1", errors="replace").strip()


def _is_high_priority(message: Message) -> bool:
    """The three competing conventions for "this one matters"."""
    priority = str(message.get("X-Priority") or "").strip()
    if priority.startswith("1"):
        return True
    if str(message.get("Importance") or "").strip().lower() == "high":
        return True
    return str(message.get("X-MSMail-Priority") or "").strip().lower() == "high"


def _sent_at(message: Message) -> datetime:
    raw = message.get("Date")
    if raw:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)
