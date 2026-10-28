"""Linear.

One GraphQL endpoint for everything, and - unlike most of the APIs here - the
API key goes in `Authorization` bare, with no `Bearer` prefix. Sending `Bearer`
gets a 400 that reads like a malformed query, so that detail is load-bearing.

GraphQL also means errors arrive with HTTP 200 and an `errors` array, so the
status code alone is never enough to know whether the call worked.
"""
from __future__ import annotations

from typing import Any

import httpx

from ..base import ActionResult, BaseConnector, FetchContext, RawSignal
from . import (
    TIMEOUT,
    ConnectorAuthError,
    ConnectorError,
    now_iso,
    parse_iso,
    raise_for_auth,
    resolve_secret,
)

API = "https://api.linear.app/graphql"
# A sprint's worth of issues. Linear's `first` is mandatory on connections, so
# this is also the pagination cap - we never walk past page one on a sweep.
ISSUE_LIMIT = 50

# Linear's own state *types* (not names, which teams rename freely). An issue in
# one of these is waiting on a decision rather than on work.
BLOCKING_STATE_TYPES = {"blocked", "triage"}
# Priority 1 is "Urgent" in Linear's fixed scale (0 none, 1 urgent ... 4 low).
URGENT_PRIORITY = 1

_ISSUES_QUERY = """
query DuetAssignedIssues($first: Int!, $filter: IssueFilter) {
  viewer {
    id
    name
    email
    assignedIssues(first: $first, filter: $filter, orderBy: updatedAt) {
      nodes {
        id
        identifier
        title
        description
        state { name type }
        priority
        dueDate
        updatedAt
        url
        creator { name displayName email }
      }
    }
  }
}
"""

_ISSUE_QUERY = """
query DuetIssue($id: String!) {
  issue(id: $id) {
    id
    identifier
    title
    description
    priority
    dueDate
    url
    state { id name type }
  }
}
"""

_CREATE_MUTATION = """
mutation DuetIssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue { id identifier title url }
  }
}
"""

_UPDATE_MUTATION = """
mutation DuetIssueUpdate($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) {
    success
    issue { id identifier title url }
  }
}
"""

_DEFAULT_TEAM_QUERY = """
query DuetDefaultTeam {
  viewer { teams(first: 1) { nodes { id key name } } }
}
"""

# Messages Linear uses for a bad key. It reports these as GraphQL errors on an
# HTTP 200, so the auth case has to be matched on text rather than status.
_AUTH_PHRASES = (
    "authentication",
    "unauthorized",
    "not authenticated",
    "invalid api key",
)


def _issue_ref(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": issue.get("id"),
        "identifier": issue.get("identifier"),
        "url": issue.get("url"),
    }


class LinearConnector(BaseConnector):
    key = "linear"

    # ------------------------------------------------------------------ #
    def _api_key(self, ctx: FetchContext) -> str | None:
        return resolve_secret(ctx, "api_key", "token", "key")

    def _headers(self, api_key: str) -> dict[str, str]:
        # No "Bearer" - see the module docstring.
        return {"Authorization": api_key, "Content-Type": "application/json"}

    def _call(
        self, client: httpx.Client, api_key: str, query: str, variables: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Run one GraphQL document. `None` means "throttled, try later"."""
        resp = client.post(
            API,
            headers=self._headers(api_key),
            json={"query": query, "variables": variables},
        )
        if resp.status_code == 429:
            return None
        raise_for_auth(self.key, resp.status_code, resp.text)
        if resp.status_code >= 400:
            raise ConnectorError(f"Linear returned HTTP {resp.status_code}")

        body = resp.json()
        errors = body.get("errors")
        if errors:
            message = "; ".join(str(e.get("message", e)) for e in errors)
            lowered = message.lower()
            if any(phrase in lowered for phrase in _AUTH_PHRASES):
                raise ConnectorAuthError(f"Linear rejected the stored API key: {message}")
            raise ConnectorError(f"Linear query failed: {message}")
        return body.get("data") or {}

    # ------------------------------------------------------------------ #
    # Read
    # ------------------------------------------------------------------ #
    def fetch(self, ctx: FetchContext) -> tuple[list[RawSignal], dict[str, Any]]:
        api_key = self._api_key(ctx)
        if not api_key:
            return [], ctx.cursor

        cursor = dict(ctx.cursor)
        # Filter server-side where we can: fetching 50 issues and discarding 48
        # of them locally burns the API's complexity budget for nothing.
        since_iso = cursor.get("updated_after")
        if not since_iso and ctx.since is not None:
            since_iso = ctx.since.isoformat()
        variables: dict[str, Any] = {"first": ISSUE_LIMIT}
        variables["filter"] = {"updatedAt": {"gt": since_iso}} if since_iso else None

        with httpx.Client(timeout=TIMEOUT) as client:
            data = self._call(client, api_key, _ISSUES_QUERY, variables)
        if data is None:
            return [], ctx.cursor

        viewer = data.get("viewer") or {}
        nodes = ((viewer.get("assignedIssues") or {}).get("nodes")) or []
        fallback_actor = viewer.get("email") or viewer.get("name") or ""

        signals: list[RawSignal] = []
        newest = since_iso
        for issue in nodes:
            signals.append(self._to_signal(issue, fallback_actor))
            updated = issue.get("updatedAt")
            if updated and (newest is None or str(updated) > str(newest)):
                newest = str(updated)

        if newest:
            cursor["updated_after"] = newest
        cursor["last_fetch"] = now_iso()
        return signals, cursor

    def _to_signal(self, issue: dict[str, Any], fallback_actor: str) -> RawSignal:
        state = issue.get("state") or {}
        state_type = str(state.get("type") or "").lower()
        priority = issue.get("priority")
        creator = issue.get("creator") or {}

        hints: dict[str, Any] = {}
        if state_type in BLOCKING_STATE_TYPES:
            hints["blocker"] = True
        if issue.get("dueDate"):
            # Linear sends a bare date ("2026-11-02"); the priority engine parses
            # it with fromisoformat, which accepts that and treats it as midnight.
            hints["deadline"] = str(issue["dueDate"])
        if priority == URGENT_PRIORITY:
            hints["importance"] = "high"

        identifier = issue.get("identifier") or ""
        return RawSignal(
            external_id=issue["id"],
            kind="ticket",
            title=issue.get("title") or identifier or "Untitled issue",
            body=(issue.get("description") or "")[:4000],
            actor=creator.get("email") or creator.get("name") or fallback_actor,
            url=issue.get("url"),
            occurred_at=parse_iso(issue.get("updatedAt")),
            raw={
                "identifier": identifier,
                "state": state.get("name"),
                "state_type": state_type,
                "priority": priority,
                "due_date": issue.get("dueDate"),
            },
            hints=hints,
        )

    # ------------------------------------------------------------------ #
    # Write
    # ------------------------------------------------------------------ #
    def execute(self, action: str, payload: dict[str, Any], ctx: FetchContext) -> ActionResult:
        api_key = self._api_key(ctx)
        if not api_key:
            return ActionResult(
                ok=False, summary="Linear is not connected", error="no_credential"
            )

        if action == "create_issue":
            return self._create_issue(api_key, payload)
        if action == "update_issue":
            return self._update_issue(api_key, payload)
        return super().execute(action, payload, ctx)

    def _create_issue(self, api_key: str, payload: dict[str, Any]) -> ActionResult:
        issue_input: dict[str, Any] = {"title": payload.get("title") or "Untitled"}
        if payload.get("description"):
            issue_input["description"] = payload["description"]
        if payload.get("priority") is not None:
            issue_input["priority"] = int(payload["priority"])
        if payload.get("assignee_id"):
            issue_input["assigneeId"] = payload["assignee_id"]
        if payload.get("due_date"):
            issue_input["dueDate"] = payload["due_date"]

        with httpx.Client(timeout=TIMEOUT) as client:
            team_id = payload.get("team_id")
            if not team_id:
                # teamId is required by Linear. Falling back to the viewer's
                # first team keeps a one-field UI working; the team used is
                # named in the summary so an approver can see where it landed.
                team_id = self._default_team_id(client, api_key)
            if not team_id:
                return ActionResult(
                    ok=False,
                    summary="Linear needs a team to create the issue in",
                    error="missing_team",
                )
            issue_input["teamId"] = team_id

            data = self._call(client, api_key, _CREATE_MUTATION, {"input": issue_input})

        if data is None:
            return ActionResult(
                ok=False, summary="Linear is rate limiting right now", error="rate_limited"
            )
        result = data.get("issueCreate") or {}
        if not result.get("success"):
            return ActionResult(
                ok=False, summary="Linear declined to create the issue", error="create_failed"
            )

        issue = result.get("issue") or {}
        # No revert plan: the catalog gives Linear no delete or archive verb, and
        # a plan pointing at an action this adapter cannot perform would make an
        # un-undoable write look undoable in the approvals UI.
        return ActionResult(
            ok=True,
            summary=(
                f"Created Linear issue {issue.get('identifier', '')}: "
                f"{issue.get('title', '')}"
            ).strip(),
            data=_issue_ref(issue),
            external_id=issue.get("id"),
        )

    def _update_issue(self, api_key: str, payload: dict[str, Any]) -> ActionResult:
        issue_id = payload.get("issue_id") or payload.get("id")
        if not issue_id:
            return ActionResult(
                ok=False, summary="Linear needs an issue id to update", error="missing_issue_id"
            )

        issue_input: dict[str, Any] = {}
        if payload.get("title"):
            issue_input["title"] = payload["title"]
        if payload.get("description") is not None:
            issue_input["description"] = payload["description"]
        if payload.get("priority") is not None:
            issue_input["priority"] = int(payload["priority"])
        if payload.get("state_id"):
            issue_input["stateId"] = payload["state_id"]
        if payload.get("due_date") is not None:
            issue_input["dueDate"] = payload["due_date"]
        if payload.get("assignee_id"):
            issue_input["assigneeId"] = payload["assignee_id"]
        if not issue_input:
            return ActionResult(
                ok=False, summary="Nothing to change on that Linear issue", error="empty_update"
            )

        with httpx.Client(timeout=TIMEOUT) as client:
            # Snapshot the fields we are about to change so the revert plan can
            # put them back.
            before = self._read_issue(client, api_key, str(issue_id))
            data = self._call(
                client, api_key, _UPDATE_MUTATION, {"id": issue_id, "input": issue_input}
            )

        if data is None:
            return ActionResult(
                ok=False, summary="Linear is rate limiting right now", error="rate_limited"
            )
        result = data.get("issueUpdate") or {}
        if not result.get("success"):
            return ActionResult(
                ok=False, summary="Linear declined to update the issue", error="update_failed"
            )

        issue = result.get("issue") or {}
        revert: dict[str, Any] = {}
        if before:
            revert = {"action": "update_issue", "issue_id": issue_id}
            for field, source in (
                ("title", "title"),
                ("description", "description"),
                ("priority", "priority"),
                ("due_date", "dueDate"),
            ):
                if field in payload or source in issue_input:
                    revert[field] = before.get(source)
            if "stateId" in issue_input:
                revert["state_id"] = (before.get("state") or {}).get("id")

        return ActionResult(
            ok=True,
            summary=f"Updated Linear issue {issue.get('identifier', issue_id)}",
            data=_issue_ref(issue),
            external_id=issue.get("id") or str(issue_id),
            revert_plan=revert,
        )

    # ------------------------------------------------------------------ #
    def _default_team_id(self, client: httpx.Client, api_key: str) -> str | None:
        data = self._call(client, api_key, _DEFAULT_TEAM_QUERY, {})
        if not data:
            return None
        nodes = (((data.get("viewer") or {}).get("teams") or {}).get("nodes")) or []
        return nodes[0].get("id") if nodes else None

    def _read_issue(
        self, client: httpx.Client, api_key: str, issue_id: str
    ) -> dict[str, Any] | None:
        """Best effort. Losing the "before" state costs a revert plan, not the write."""
        try:
            data = self._call(client, api_key, _ISSUE_QUERY, {"id": issue_id})
        except ConnectorError:
            return None
        if not data:
            return None
        return data.get("issue")
