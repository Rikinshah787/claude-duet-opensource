"""Slack.

The trap this adapter exists to not fall into: **Slack answers HTTP 200 for
errors.** A revoked token, a missing scope and a channel the bot was kicked from
all come back as `200 {"ok": false, "error": "..."}`. Checking `resp.status_code`
alone reports an empty-but-healthy workspace, which is the worst possible
failure mode for a proactive assistant - it looks like silence.

Only messages that are actually *for* the user become signals. Emitting every
message in every channel would bury the briefing, so the filter is: direct
messages, explicit `<@U...>` mentions, and `@here`/`@channel` broadcasts (marked
as broadcasts so the priority engine can discount them).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from ..base import ActionResult, BaseConnector, FetchContext, RawSignal
from . import (
    TIMEOUT,
    ConnectorAuthError,
    ConnectorError,
    now_iso,
    parse_epoch,
    raise_for_auth,
    resolve_secret,
)

log = logging.getLogger("duet.connectors.slack")

API = "https://slack.com/api"

CHANNEL_LIMIT = 50
HISTORY_LIMIT = 20
# How many conversations one sweep will read history from. Reading 50 channels
# per sweep is both slow and rate-limit bait; the newest handful carries almost
# all of the signal.
CHANNELS_PER_SWEEP = 8

# Slack error codes that mean the credential itself is no good, as opposed to a
# per-call problem we can skip past.
AUTH_ERRORS = {
    "invalid_auth",
    "not_authed",
    "account_inactive",
    "token_revoked",
    "token_expired",
    "no_permission",
    "missing_scope",
}

# `<!here>` / `<!channel>` are how Slack encodes the broadcast keywords on the
# wire; the raw "@here" text never appears in message JSON.
BROADCAST_TOKENS = ("<!here>", "<!channel>", "<!everyone>", "@here", "@channel")

SKIP_SUBTYPES = {
    "channel_join",
    "channel_leave",
    "channel_topic",
    "channel_purpose",
    "channel_name",
    "bot_message",
}


class SlackConnector(BaseConnector):
    key = "slack"

    # ------------------------------------------------------------------ #
    def _token(self, ctx: FetchContext) -> str | None:
        return resolve_secret(ctx, "bot_token", "token", "access_token")

    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _call(
        self,
        client: httpx.Client,
        token: str,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One Web API call, with Slack's `ok` field treated as the real status.

        Raises on auth failures and on an unexpected `ok: false`. Returns the
        body so callers that want to tolerate a specific error can inspect it -
        they pass `tolerate` through `_try_call` instead.
        """
        if json_body is not None:
            resp = client.post(
                f"{API}/{method}",
                headers={**self._headers(token), "Content-Type": "application/json; charset=utf-8"},
                json=json_body,
            )
        else:
            resp = client.get(f"{API}/{method}", headers=self._headers(token), params=params or {})

        if resp.status_code == 429:
            return {"ok": False, "error": "ratelimited"}
        raise_for_auth(self.key, resp.status_code, resp.text)
        if resp.status_code >= 400:
            raise ConnectorError(f"Slack {method} failed (HTTP {resp.status_code})")

        body = resp.json()
        if not body.get("ok"):
            error = str(body.get("error", "unknown_error"))
            if error in AUTH_ERRORS:
                raise ConnectorAuthError(f"Slack rejected the stored token: {error}")
            if error == "ratelimited":
                return body
            raise ConnectorError(f"Slack {method} returned not-ok: {error}")
        return body

    # ------------------------------------------------------------------ #
    # Read
    # ------------------------------------------------------------------ #
    def fetch(self, ctx: FetchContext) -> tuple[list[RawSignal], dict[str, Any]]:
        token = self._token(ctx)
        if not token:
            return [], ctx.cursor

        cursor = dict(ctx.cursor)
        signals: list[RawSignal] = []

        with httpx.Client(timeout=TIMEOUT) as client:
            # auth.test does double duty: it is how we learn our own user id
            # (needed to recognise a mention) and the cheapest credential check.
            identity = self._call(client, token, "auth.test")
            if identity.get("error") == "ratelimited":
                return [], ctx.cursor
            user_id = str(identity.get("user_id") or "")
            team_url = str(identity.get("url") or "").rstrip("/")
            cursor["user_id"] = user_id
            cursor["team"] = identity.get("team")

            listing = self._call(
                client,
                token,
                "conversations.list",
                {
                    # `im` is included so direct messages are reachable at all -
                    # without it there is no conversation of type `im` to read.
                    "types": "public_channel,private_channel,im",
                    "limit": CHANNEL_LIMIT,
                    "exclude_archived": "true",
                },
            )
            if listing.get("error") == "ratelimited":
                return [], ctx.cursor

            channels = listing.get("channels", []) or []
            # Prefer the conversations with recent activity; Slack orders this
            # list by creation, which has nothing to do with relevance.
            channels.sort(key=lambda c: float(c.get("updated", 0) or 0), reverse=True)

            oldest = cursor.get("oldest_ts")
            if not oldest and ctx.since is not None:
                oldest = f"{ctx.since.timestamp():.6f}"

            newest_seen = float(oldest or 0)
            for channel in channels[:CHANNELS_PER_SWEEP]:
                channel_id = channel.get("id")
                if not channel_id:
                    continue
                history = self._try_call(
                    client,
                    token,
                    "conversations.history",
                    {
                        "channel": channel_id,
                        "limit": HISTORY_LIMIT,
                        **({"oldest": oldest} if oldest else {}),
                    },
                )
                if history is None:
                    continue  # not_in_channel and friends: skip, keep sweeping
                if history.get("error") == "ratelimited":
                    break  # stop asking; return what we already have

                for message in history.get("messages", []) or []:
                    signal = self._to_signal(message, channel, user_id, team_url)
                    if signal is None:
                        continue
                    signals.append(signal)
                    newest_seen = max(newest_seen, float(message.get("ts", 0) or 0))

            if newest_seen:
                cursor["oldest_ts"] = f"{newest_seen:.6f}"

        cursor["last_fetch"] = now_iso()
        return signals, cursor

    def _try_call(
        self, client: httpx.Client, token: str, method: str, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        """A call whose failure should not end the sweep.

        One channel the bot cannot read must not cost us the other seven, so a
        per-call Slack error is logged and skipped. Auth errors still propagate,
        because those are about the whole connection.
        """
        try:
            return self._call(client, token, method, params)
        except ConnectorAuthError:
            raise
        except (ConnectorError, httpx.HTTPError) as exc:
            log.info("slack %s skipped for %s: %s", method, params.get("channel"), exc)
            return None

    def _to_signal(
        self,
        message: dict[str, Any],
        channel: dict[str, Any],
        user_id: str,
        team_url: str,
    ) -> RawSignal | None:
        if message.get("subtype") in SKIP_SUBTYPES:
            return None
        author = str(message.get("user") or "")
        if author and author == user_id:
            return None  # our own messages are not news

        text = str(message.get("text") or "")
        ts = str(message.get("ts") or "")
        if not ts:
            return None

        is_dm = bool(channel.get("is_im"))
        mentioned = bool(user_id) and f"<@{user_id}>" in text
        broadcast = any(token in text for token in BROADCAST_TOKENS)

        hints: dict[str, Any] = {}
        if is_dm:
            kind = "dm"
            hints["dm"] = True
        elif mentioned:
            kind = "mention"
            hints["mention"] = True
        elif broadcast:
            # Addressed at a room, not at the user. Kept because an @channel can
            # matter, but `noise_factor` docks it for not being personal.
            kind = "mention"
        else:
            return None

        if broadcast:
            hints["channel_broadcast"] = True
        if mentioned:
            hints["mention"] = True

        channel_name = channel.get("name") or ("direct message" if is_dm else channel.get("id"))
        label = f"DM from {author or 'someone'}" if is_dm else f"#{channel_name}"
        first_line = text.strip().splitlines()[0] if text.strip() else "(no text)"

        return RawSignal(
            # Slack message identity is channel + ts; ts alone repeats across
            # channels.
            external_id=f"{channel.get('id')}-{ts}",
            kind=kind,
            title=f"{label}: {first_line[:160]}",
            body=text[:4000],
            actor=author or str(message.get("username") or ""),
            url=_permalink(team_url, str(channel.get("id") or ""), ts),
            occurred_at=parse_epoch(ts),
            raw={
                "channel": channel.get("id"),
                "channel_name": channel.get("name"),
                "ts": ts,
                "thread_ts": message.get("thread_ts"),
                "reply_count": message.get("reply_count", 0),
            },
            hints=hints,
        )

    # ------------------------------------------------------------------ #
    # Write
    # ------------------------------------------------------------------ #
    def execute(self, action: str, payload: dict[str, Any], ctx: FetchContext) -> ActionResult:
        token = self._token(ctx)
        if not token:
            return ActionResult(
                ok=False, summary="Slack is not connected", error="no_credential"
            )

        if action == "post_message":
            return self._post_message(token, payload)
        if action == "set_status":
            return self._set_status(token, payload)
        return super().execute(action, payload, ctx)

    def _post_message(self, token: str, payload: dict[str, Any]) -> ActionResult:
        channel = payload.get("channel") or payload.get("channel_id")
        text = payload.get("text") or payload.get("message")
        if not channel or not text:
            return ActionResult(
                ok=False,
                summary="Slack needs a channel and a message to post",
                error="missing_fields",
            )

        body: dict[str, Any] = {"channel": channel, "text": text}
        if payload.get("thread_ts"):
            body["thread_ts"] = payload["thread_ts"]

        with httpx.Client(timeout=TIMEOUT) as client:
            try:
                result = self._call(client, token, "chat.postMessage", json_body=body)
            except ConnectorError as exc:
                return ActionResult(
                    ok=False, summary=f"Slack refused to post: {exc}", error="post_failed"
                )

        ts = str(result.get("ts") or "")
        # No revert plan: the catalog marks post_message irreversible, and
        # chat.delete is not a verb this connector is allowed to offer.
        return ActionResult(
            ok=True,
            summary=f"Posted to {channel}",
            data={"channel": result.get("channel"), "ts": ts},
            external_id=f"{result.get('channel')}-{ts}" if ts else None,
        )

    def _set_status(self, token: str, payload: dict[str, Any]) -> ActionResult:
        status_text = payload.get("status_text", payload.get("text", ""))
        status_emoji = payload.get("status_emoji", "")
        profile: dict[str, Any] = {
            "status_text": status_text,
            "status_emoji": status_emoji,
        }
        if payload.get("status_expiration") is not None:
            profile["status_expiration"] = int(payload["status_expiration"])

        with httpx.Client(timeout=TIMEOUT) as client:
            previous = self._try_call(client, token, "users.profile.get", {})
            try:
                result = self._call(
                    client, token, "users.profile.set", json_body={"profile": profile}
                )
            except ConnectorError as exc:
                return ActionResult(
                    ok=False,
                    summary=f"Slack refused to set your status: {exc}",
                    error="status_failed",
                )

        revert: dict[str, Any] = {}
        if previous and previous.get("profile"):
            old = previous["profile"]
            revert = {
                "action": "set_status",
                "status_text": old.get("status_text", ""),
                "status_emoji": old.get("status_emoji", ""),
            }

        return ActionResult(
            ok=True,
            summary=(
                f"Slack status set to '{status_text}'" if status_text else "Slack status cleared"
            ),
            data={"profile": (result.get("profile") or {}).get("status_text", status_text)},
            revert_plan=revert,
        )


def _permalink(team_url: str, channel_id: str, ts: str) -> str | None:
    """Rebuild the archive link rather than spend a chat.getPermalink call per message."""
    if not team_url or not channel_id or not ts:
        return None
    return f"{team_url}/archives/{channel_id}/p{ts.replace('.', '')}"
