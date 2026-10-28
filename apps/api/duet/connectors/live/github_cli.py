"""GitHub, for real, through the `gh` CLI.

Why the CLI rather than the REST API: anyone who works on GitHub from a terminal
already has `gh` installed and authenticated, so this connector needs no token,
no OAuth app and no setup step. It reuses an auth the user already granted,
which is the same reasoning behind the Claude CLI provider.

A token-based adapter is still the right answer for a server deployment, and
`_token_mode` is the seam for it - the parsing below works off `gh api` JSON,
which is byte-identical to what the REST API returns.

Everything here is read-only except `execute`, which is only ever reached
through the approval gate.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any

from ..base import ActionResult, BaseConnector, FetchContext, RawSignal

log = logging.getLogger("duet.connectors.github")

# `gh` can sit outside PATH for the API process even when a shell finds it.
_CANDIDATES = (
    "gh",
    r"C:\Program Files\GitHub CLI\gh.exe",
    r"C:\Program Files (x86)\GitHub CLI\gh.exe",
    "/usr/bin/gh",
    "/usr/local/bin/gh",
    "/opt/homebrew/bin/gh",
)


def find_gh() -> str | None:
    found = shutil.which("gh")
    if found:
        return found
    import os

    for path in _CANDIDATES[1:]:
        if os.path.exists(path):
            return path
    return None


def gh_authenticated() -> tuple[bool, str]:
    gh = find_gh()
    if not gh:
        return False, "gh CLI not installed"
    try:
        proc = subprocess.run(
            [gh, "auth", "status"], capture_output=True, text=True, timeout=15
        )
        if proc.returncode != 0:
            return False, "gh CLI is installed but not signed in (`gh auth login`)"
        return True, "gh CLI authenticated"
    except Exception as exc:
        return False, f"gh check failed: {type(exc).__name__}"


def _gh_json(args: list[str], *, timeout: float = 30.0) -> Any:
    """Run `gh` and parse JSON. Returns None rather than raising on failure."""
    gh = find_gh()
    if not gh:
        return None
    try:
        proc = subprocess.run(
            [gh, *args], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log.warning("gh %s timed out", " ".join(args[:2]))
        return None
    if proc.returncode != 0:
        log.warning("gh %s failed: %s", " ".join(args[:2]), (proc.stderr or "")[:200])
        return None
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError:
        return None


def _parse_ts(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


class GitHubConnector(BaseConnector):
    key = "github"

    # Notification reasons worth a person's attention, mapped to the signal
    # kinds the priority engine knows how to weigh.
    _REASON_KINDS = {
        "review_requested": "review_request",
        "mention": "mention",
        "assign": "ticket",
        "team_mention": "mention",
        "ci_activity": "ci_failure",
        "author": "thread_waiting",
        "comment": "mention",
        "state_change": "ticket",
    }

    def fetch(self, ctx: FetchContext) -> tuple[list[RawSignal], dict[str, Any]]:
        ok, _ = gh_authenticated()
        if not ok:
            return [], ctx.cursor

        signals: list[RawSignal] = []
        signals.extend(self._notifications(ctx))
        signals.extend(self._review_requests())

        cursor = dict(ctx.cursor)
        cursor["last_fetch"] = datetime.now(timezone.utc).isoformat()
        return signals, cursor

    # ------------------------------------------------------------------ #
    def _notifications(self, ctx: FetchContext) -> list[RawSignal]:
        rows = _gh_json(["api", "notifications", "--paginate"]) or []
        if not isinstance(rows, list):
            return []

        out: list[RawSignal] = []
        for n in rows[:50]:
            subject = n.get("subject") or {}
            repo = (n.get("repository") or {}).get("full_name", "")
            reason = n.get("reason", "")
            title = subject.get("title") or "(no title)"
            kind = self._REASON_KINDS.get(reason, "mention")

            hints: dict[str, Any] = {"external": True}
            if reason in {"review_requested", "assign"}:
                # Someone is blocked until this is looked at.
                hints["blocker"] = True
            if reason in {"mention", "team_mention"}:
                hints["mention"] = True
            if subject.get("type") == "CheckSuite" or reason == "ci_activity":
                hints["ci"] = True
            if n.get("unread"):
                hints["unread"] = True

            out.append(
                RawSignal(
                    external_id=f"gh-notif-{n.get('id')}",
                    kind=kind,
                    title=f"{repo}: {title}" if repo else title,
                    body=f"{reason.replace('_', ' ')} on a {subject.get('type', 'thread')}",
                    actor=repo,
                    url=self._web_url(subject.get("url"), repo),
                    occurred_at=_parse_ts(n.get("updated_at")),
                    raw={"reason": reason, "type": subject.get("type")},
                    hints=hints,
                )
            )
        return out

    def _review_requests(self) -> list[RawSignal]:
        """PRs explicitly waiting on this user - the highest-signal GitHub item."""
        rows = _gh_json(
            [
                "search", "prs", "--review-requested=@me", "--state=open",
                "--limit", "20", "--json",
                "title,url,repository,createdAt,author,number",
            ]
        ) or []
        if not isinstance(rows, list):
            return []

        out: list[RawSignal] = []
        for pr in rows:
            repo = (pr.get("repository") or {}).get("nameWithOwner", "")
            author = (pr.get("author") or {}).get("login", "")
            out.append(
                RawSignal(
                    external_id=f"gh-pr-{repo}-{pr.get('number')}",
                    kind="review_request",
                    title=f"{repo}#{pr.get('number')}: {pr.get('title', '')}",
                    body=f"{author} is waiting on your review.",
                    actor=author,
                    url=pr.get("url"),
                    occurred_at=_parse_ts(pr.get("createdAt")),
                    hints={"review": True, "blocker": True, "external": True},
                )
            )
        return out

    @staticmethod
    def _web_url(api_url: str | None, repo: str) -> str | None:
        """Notifications carry api.github.com URLs; people want the web one."""
        if not api_url:
            return f"https://github.com/{repo}" if repo else None
        return (
            api_url.replace("https://api.github.com/repos/", "https://github.com/")
            .replace("/pulls/", "/pull/")
        )

    # ------------------------------------------------------------------ #
    def execute(self, action: str, payload: dict[str, Any], ctx: FetchContext) -> ActionResult:
        """Only reached after a human approved this exact action."""
        if action == "comment":
            target = payload.get("url") or payload.get("issue")
            body = payload.get("body") or payload.get("description") or ""
            if not target or not body:
                return ActionResult(ok=False, summary="need a target and a body",
                                    error="bad_payload")
            result = _gh_json(
                ["api", "-X", "POST", f"{_issue_api(target)}/comments", "-f", f"body={body}"]
            )
            if result is None:
                return ActionResult(ok=False, summary="gh rejected the comment",
                                    error="gh_failed")
            return ActionResult(
                ok=True, summary=f"Commented on {target}",
                data={"url": result.get("html_url")},
                external_id=str(result.get("id")),
            )

        if action == "create_issue":
            repo = payload.get("repo")
            title = payload.get("title", "")
            if not repo:
                return ActionResult(ok=False, summary="no repo given", error="bad_payload")
            result = _gh_json(
                ["api", "-X", "POST", f"repos/{repo}/issues",
                 "-f", f"title={title}",
                 "-f", f"body={payload.get('description', '')}"]
            )
            if result is None:
                return ActionResult(ok=False, summary="gh rejected the issue",
                                    error="gh_failed")
            return ActionResult(
                ok=True, summary=f"Opened {repo}#{result.get('number')}",
                data={"url": result.get("html_url")},
                external_id=str(result.get("number")),
                revert_plan={"action": "close_issue", "repo": repo,
                             "number": result.get("number")},
            )

        if action == "close_issue":  # the revert path
            repo, number = payload.get("repo"), payload.get("number")
            _gh_json(["api", "-X", "PATCH", f"repos/{repo}/issues/{number}",
                      "-f", "state=closed"])
            return ActionResult(ok=True, summary=f"Closed {repo}#{number}")

        # merge_pr is declared SENSITIVE in the catalog and deliberately not
        # implemented here: merging from an assistant is a bigger step than this
        # connector should take on its own, approval or not.
        return ActionResult(
            ok=False, summary=f"github.{action} is not implemented", error="not_implemented"
        )


def _issue_api(url: str) -> str:
    """Turn a web URL into the repos/.../issues/N form `gh api` wants."""
    path = url.replace("https://github.com/", "").replace("/pull/", "/issues/")
    return f"repos/{path}"
