"""Notion.

Read path is `POST /search` sorted by `last_edited_time`, which is the only
endpoint that spans every page the integration can see without knowing the
workspace layout first. The integration only ever sees pages a human explicitly
shared with it, so "search everything" is already scoped by Notion.

Writes go through the page endpoints. Deleting a page in Notion means archiving
it, which is why `delete_page` is reversible here and ships a revert plan that
un-archives.
"""
from __future__ import annotations

from typing import Any

import httpx

from ..base import ActionResult, BaseConnector, FetchContext, RawSignal
from . import (
    TIMEOUT,
    ConnectorError,
    now_iso,
    parse_iso,
    raise_for_auth,
    resolve_secret,
)

API = "https://api.notion.com/v1"
# Pinned deliberately. Notion versions its API by date and an unpinned client
# breaks on their schedule rather than ours.
NOTION_VERSION = "2022-06-28"
# One sweep's ceiling. Notion's search is workspace-wide, so without a cap a
# large wiki would pull thousands of rows nobody asked about.
PAGE_SIZE = 50


class NotionConnector(BaseConnector):
    key = "notion"

    # ------------------------------------------------------------------ #
    # Credentials
    # ------------------------------------------------------------------ #
    def _token(self, ctx: FetchContext) -> str | None:
        return resolve_secret(ctx, "token", "api_key", "access_token")

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------ #
    # Read
    # ------------------------------------------------------------------ #
    def fetch(self, ctx: FetchContext) -> tuple[list[RawSignal], dict[str, Any]]:
        token = self._token(ctx)
        if not token:
            return [], ctx.cursor

        cursor = dict(ctx.cursor)
        # Notion's `last_edited_time` strings are fixed-width ISO8601 in UTC, so
        # comparing the cursor as a string is correct and avoids reparsing a
        # value we wrote ourselves. `ctx.since` is a datetime from a different
        # source, so it gets compared as one rather than string-mixed with this.
        watermark: str | None = cursor.get("last_edited_time")

        body = {
            "sort": {"direction": "descending", "timestamp": "last_edited_time"},
            "page_size": PAGE_SIZE,
        }

        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(f"{API}/search", headers=self._headers(token), json=body)
            if resp.status_code == 429:
                # Leave the cursor untouched so the next sweep covers this window.
                return [], ctx.cursor
            raise_for_auth(self.key, resp.status_code, resp.text)
            if resp.status_code >= 400:
                raise ConnectorError(f"Notion search failed (HTTP {resp.status_code})")
            payload = resp.json()

        signals: list[RawSignal] = []
        newest = watermark
        for obj in payload.get("results", []):
            if obj.get("object") != "page":
                continue  # search also returns databases; those are not edits
            edited = obj.get("last_edited_time")
            if watermark and edited and edited <= watermark:
                continue
            if ctx.since is not None:
                edited_at = parse_iso(edited)
                if edited_at and edited_at <= ctx.since:
                    continue
            signals.append(self._to_signal(obj))
            if edited and (newest is None or edited > newest):
                newest = edited

        if newest:
            cursor["last_edited_time"] = newest
        cursor["last_fetch"] = now_iso()
        return signals, cursor

    def _to_signal(self, page: dict[str, Any]) -> RawSignal:
        properties = page.get("properties") or {}
        title = _page_title(page)
        editor = (page.get("last_edited_by") or {}).get("id", "")

        hints: dict[str, Any] = {}
        deadline = _first_due_date(properties)
        if deadline:
            # A database row with a due date is the one case where a doc edit
            # carries real time pressure; the priority engine reads `deadline`.
            hints["deadline"] = deadline

        return RawSignal(
            external_id=page["id"],
            kind="doc_update",
            title=title,
            body=f"Edited in Notion at {page.get('last_edited_time', 'an unknown time')}.",
            actor=editor,
            url=page.get("url"),
            occurred_at=parse_iso(page.get("last_edited_time")),
            raw={
                "last_edited_time": page.get("last_edited_time"),
                "created_time": page.get("created_time"),
                "archived": page.get("archived", False),
                "parent": page.get("parent", {}),
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
                ok=False,
                summary="Notion is not connected",
                error="no_credential",
            )

        if action == "create_page":
            return self._create_page(token, payload)
        if action == "update_page":
            return self._update_page(token, payload)
        if action == "delete_page":
            return self._delete_page(token, payload)
        return super().execute(action, payload, ctx)

    def _create_page(self, token: str, payload: dict[str, Any]) -> ActionResult:
        parent = payload.get("parent")
        if not parent:
            if payload.get("database_id"):
                parent = {"database_id": payload["database_id"]}
            elif payload.get("parent_id") or payload.get("page_id"):
                parent = {"page_id": payload.get("parent_id") or payload["page_id"]}
        if not parent:
            return ActionResult(
                ok=False,
                summary="Notion needs a parent page or database to create a page in",
                error="missing_parent",
            )

        properties = payload.get("properties")
        if not properties:
            properties = {
                "title": {"title": [{"text": {"content": payload.get("title", "Untitled")}}]}
            }

        body: dict[str, Any] = {"parent": parent, "properties": properties}
        if payload.get("children"):
            body["children"] = payload["children"]

        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(f"{API}/pages", headers=self._headers(token), json=body)
            result = self._guard(resp, "create a page")
            if isinstance(result, ActionResult):
                return result
            page = result

        page_id = page.get("id", "")
        return ActionResult(
            ok=True,
            summary=f"Created Notion page '{_page_title(page)}'",
            data={"id": page_id, "url": page.get("url")},
            external_id=page_id,
            # Notion has no hard delete through the API, so undoing a create
            # means archiving the page back out of sight.
            revert_plan={"action": "delete_page", "page_id": page_id},
        )

    def _update_page(self, token: str, payload: dict[str, Any]) -> ActionResult:
        page_id = payload.get("page_id") or payload.get("id")
        if not page_id:
            return ActionResult(
                ok=False, summary="Notion needs a page id to update", error="missing_page_id"
            )

        body: dict[str, Any] = {}
        if payload.get("properties"):
            body["properties"] = payload["properties"]
        elif payload.get("title"):
            body["properties"] = {
                "title": {"title": [{"text": {"content": payload["title"]}}]}
            }
        if "archived" in payload:
            body["archived"] = bool(payload["archived"])
        if not body:
            return ActionResult(
                ok=False, summary="Nothing to change on that Notion page", error="empty_update"
            )

        with httpx.Client(timeout=TIMEOUT) as client:
            # Read the page first so the revert plan carries the values we are
            # about to overwrite. Without this an "undo" would have nothing to
            # restore to.
            previous = self._read_page(client, token, page_id)
            resp = client.patch(
                f"{API}/pages/{page_id}", headers=self._headers(token), json=body
            )
            result = self._guard(resp, "update a page")
            if isinstance(result, ActionResult):
                return result
            page = result

        revert: dict[str, Any] = {}
        if previous is not None:
            revert = {"action": "update_page", "page_id": page_id}
            if "properties" in body:
                revert["properties"] = previous.get("properties", {})
            if "archived" in body:
                revert["archived"] = previous.get("archived", False)

        return ActionResult(
            ok=True,
            summary=f"Updated Notion page '{_page_title(page)}'",
            data={"id": page.get("id"), "url": page.get("url")},
            external_id=page.get("id"),
            revert_plan=revert,
        )

    def _delete_page(self, token: str, payload: dict[str, Any]) -> ActionResult:
        page_id = payload.get("page_id") or payload.get("id")
        if not page_id:
            return ActionResult(
                ok=False, summary="Notion needs a page id to delete", error="missing_page_id"
            )

        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.patch(
                f"{API}/pages/{page_id}",
                headers=self._headers(token),
                json={"archived": True},
            )
            result = self._guard(resp, "delete a page")
            if isinstance(result, ActionResult):
                return result
            page = result

        return ActionResult(
            ok=True,
            summary=f"Archived Notion page '{_page_title(page)}'",
            data={"id": page.get("id"), "archived": True},
            external_id=page.get("id"),
            revert_plan={"action": "update_page", "page_id": page_id, "archived": False},
        )

    # ------------------------------------------------------------------ #
    def _read_page(
        self, client: httpx.Client, token: str, page_id: str
    ) -> dict[str, Any] | None:
        """Best effort. A missing "before" state costs us a revert plan, not the write."""
        try:
            resp = client.get(f"{API}/pages/{page_id}", headers=self._headers(token))
        except httpx.HTTPError:
            return None
        if resp.status_code >= 400:
            return None
        return resp.json()

    def _guard(self, resp: httpx.Response, what: str) -> dict[str, Any] | ActionResult:
        """Auth errors raise; everything else becomes a failed-but-reportable result."""
        raise_for_auth(self.key, resp.status_code, resp.text)
        if resp.status_code >= 400:
            message = ""
            try:
                message = resp.json().get("message", "")
            except ValueError:
                message = resp.text[:200]
            return ActionResult(
                ok=False,
                summary=f"Notion refused to {what}: {message or resp.status_code}",
                error=f"http_{resp.status_code}",
            )
        return resp.json()


# --------------------------------------------------------------------------- #
# Property plucking
# --------------------------------------------------------------------------- #
def _rich_text(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            out.append(item.get("plain_text") or (item.get("text") or {}).get("content") or "")
    return "".join(out).strip()


def _page_title(page: dict[str, Any]) -> str:
    """Notion has no title field - it is a property whose *name* varies.

    Pages in a database usually call it "Name"; standalone pages call it
    "title". Rather than guess, prefer those two and otherwise take whichever
    property declares `type: "title"`, since Notion permits exactly one.
    """
    properties = page.get("properties") or {}
    for name in ("title", "Name", "Title"):
        prop = properties.get(name)
        if isinstance(prop, dict) and prop.get("type") == "title":
            text = _rich_text(prop.get("title"))
            if text:
                return text
    for prop in properties.values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            text = _rich_text(prop.get("title"))
            if text:
                return text
    child = page.get("child_page") or {}
    return child.get("title") or "Untitled"


def _first_due_date(properties: dict[str, Any]) -> str | None:
    """A date property that reads like a deadline, if the page has one."""
    for name, prop in properties.items():
        if not isinstance(prop, dict) or prop.get("type") != "date":
            continue
        if not any(word in name.lower() for word in ("due", "deadline", "ship")):
            continue
        start = (prop.get("date") or {}).get("start")
        if start:
            return str(start)
    return None
