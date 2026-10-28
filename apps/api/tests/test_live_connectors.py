"""The live connector adapters, with the network stubbed out.

These tests exist for one reason: an adapter that mis-shapes a signal is
invisible until a briefing says the wrong thing. The assertions that matter most
are the hint ones - a Stripe amount off by 100x, an undetected Slack mention or
a mojibake subject line all score correctly-wrong, and nothing downstream can
recover from that.

No test here touches the network. `httpx` is replaced per-module with a stub
that replays canned vendor responses and records every request, which also lets
a test assert that a forbidden action made *no* call at all.
"""
from __future__ import annotations

import base64
import imaplib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from duet.connectors.base import FetchContext
from duet.connectors.live import ConnectorAuthError, ConnectorError
from duet.connectors.live.imap_mail import ImapConnector
from duet.connectors.live.linear import LinearConnector
from duet.connectors.live.notion import NotionConnector
from duet.connectors.live.slack import SlackConnector
from duet.connectors.live.stripe import StripeConnector
from duet.core.vault import get_vault


# --------------------------------------------------------------------------- #
# HTTP stub
# --------------------------------------------------------------------------- #
class StubResponse:
    def __init__(self, status_code: int = 200, payload: Any = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (str(payload) if payload is not None else "")

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class StubClient:
    """Replays responses by (method, URL substring) and records what was sent."""

    def __init__(self, routes: list[tuple[str, str, Any]], calls: list[dict[str, Any]]):
        self._routes = routes
        self.calls = calls

    def __enter__(self) -> StubClient:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def _dispatch(self, method: str, url: str, kwargs: dict[str, Any]) -> StubResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        for route_method, fragment, response in self._routes:
            if route_method == method and fragment in url:
                return response(kwargs) if callable(response) else response
        raise AssertionError(f"unrouted {method} {url}")

    def get(self, url: str, **kwargs: Any) -> StubResponse:
        return self._dispatch("GET", url, kwargs)

    def post(self, url: str, **kwargs: Any) -> StubResponse:
        return self._dispatch("POST", url, kwargs)

    def patch(self, url: str, **kwargs: Any) -> StubResponse:
        return self._dispatch("PATCH", url, kwargs)


def stub_http(
    monkeypatch: pytest.MonkeyPatch, module: Any, routes: list[tuple[str, str, Any]]
) -> list[dict[str, Any]]:
    """Swap one adapter module's `httpx` for the stub. Returns the call log.

    Patching the module attribute rather than `httpx.Client` itself keeps the
    replacement scoped to the adapter under test.
    """
    calls: list[dict[str, Any]] = []
    fake = SimpleNamespace(
        Client=lambda **_kwargs: StubClient(routes, calls),
        HTTPError=httpx.HTTPError,
        Response=httpx.Response,
    )
    monkeypatch.setattr(module, "httpx", fake)
    return calls


def make_ctx(
    connector_key: str,
    credential: dict[str, Any] | None = None,
    *,
    cursor: dict[str, Any] | None = None,
    since: datetime | None = None,
) -> FetchContext:
    """A FetchContext whose credential really is in the real vault."""
    handle = None
    if credential is not None:
        handle = get_vault().put("live-test-user", connector_key, credential)
    return FetchContext(
        user_id="live-test-user",
        connection_id="conn-live",
        since=since,
        cursor=cursor or {},
        credential=handle,
    )


# --------------------------------------------------------------------------- #
# Notion
# --------------------------------------------------------------------------- #
NOTION_SEARCH = {
    "object": "list",
    "results": [
        {
            "object": "page",
            "id": "page-aaa",
            "url": "https://notion.so/page-aaa",
            "created_time": "2026-10-20T09:00:00.000Z",
            "last_edited_time": "2026-10-25T14:03:00.000Z",
            "last_edited_by": {"object": "user", "id": "user-dana"},
            "archived": False,
            "properties": {
                "title": {
                    "id": "title",
                    "type": "title",
                    "title": [{"plain_text": "Series A data room checklist"}],
                }
            },
        },
        {
            "object": "page",
            "id": "page-bbb",
            "url": "https://notion.so/page-bbb",
            "last_edited_time": "2026-10-24T08:00:00.000Z",
            "last_edited_by": {"object": "user", "id": "user-lena"},
            "properties": {
                # Database rows call the title property "Name", and a date
                # property named "Due" is the deadline the engine should see.
                "Name": {
                    "id": "title",
                    "type": "title",
                    "title": [{"plain_text": "Renew Northwind contract"}],
                },
                "Due": {"id": "d1", "type": "date", "date": {"start": "2026-10-30"}},
            },
        },
        # A database, not a page. Not an edit; must not become a signal.
        {"object": "database", "id": "db-ccc", "last_edited_time": "2026-10-26T01:00:00.000Z"},
    ],
}


class TestNotion:
    def test_no_credential_is_silent(self):
        adapter = NotionConnector()
        signals, cursor = adapter.fetch(make_ctx("notion", cursor={"x": 1}))
        assert signals == []
        assert cursor == {"x": 1}

    def test_fetch_maps_pages_to_doc_updates(self, monkeypatch):
        stub_http(
            monkeypatch,
            __import__("duet.connectors.live.notion", fromlist=["x"]),
            [("POST", "/search", StubResponse(200, NOTION_SEARCH))],
        )
        adapter = NotionConnector()
        signals, cursor = adapter.fetch(make_ctx("notion", {"token": "secret_abc"}))

        assert [s.external_id for s in signals] == ["page-aaa", "page-bbb"]
        assert {s.kind for s in signals} == {"doc_update"}

        first, second = signals
        assert first.title == "Series A data room checklist"
        assert first.url == "https://notion.so/page-aaa"
        assert first.actor == "user-dana"
        # Title lives under "Name" on a database row.
        assert second.title == "Renew Northwind contract"
        # A "Due" date property is a real deadline for the priority engine.
        assert second.hints["deadline"] == "2026-10-30"
        # Watermark is the newest page edit, so the next sweep skips both.
        assert cursor["last_edited_time"] == "2026-10-25T14:03:00.000Z"

    def test_cursor_watermark_filters_already_seen_pages(self, monkeypatch):
        stub_http(
            monkeypatch,
            __import__("duet.connectors.live.notion", fromlist=["x"]),
            [("POST", "/search", StubResponse(200, NOTION_SEARCH))],
        )
        adapter = NotionConnector()
        signals, _ = adapter.fetch(
            make_ctx(
                "notion",
                {"token": "secret_abc"},
                cursor={"last_edited_time": "2026-10-24T08:00:00.000Z"},
            )
        )
        assert [s.external_id for s in signals] == ["page-aaa"]

    def test_rejected_token_raises(self, monkeypatch):
        stub_http(
            monkeypatch,
            __import__("duet.connectors.live.notion", fromlist=["x"]),
            [("POST", "/search", StubResponse(401, {"message": "API token is invalid."}))],
        )
        adapter = NotionConnector()
        with pytest.raises(ConnectorAuthError):
            adapter.fetch(make_ctx("notion", {"token": "stale"}))

    def test_throttling_returns_what_we_have(self, monkeypatch):
        stub_http(
            monkeypatch,
            __import__("duet.connectors.live.notion", fromlist=["x"]),
            [("POST", "/search", StubResponse(429, {"message": "rate limited"}))],
        )
        adapter = NotionConnector()
        before = {"last_edited_time": "2026-10-01T00:00:00.000Z"}
        signals, cursor = adapter.fetch(make_ctx("notion", {"token": "t"}, cursor=before))
        # Cursor must not advance, so the next sweep retries the same window.
        assert signals == []
        assert cursor == before

    def test_create_page_succeeds_and_plans_its_own_undo(self, monkeypatch):
        created = {
            "id": "page-new",
            "url": "https://notion.so/page-new",
            "properties": {
                "title": {"type": "title", "title": [{"plain_text": "Board update draft"}]}
            },
        }
        calls = stub_http(
            monkeypatch,
            __import__("duet.connectors.live.notion", fromlist=["x"]),
            [("POST", "/pages", StubResponse(200, created))],
        )
        adapter = NotionConnector()
        result = adapter.execute(
            "create_page",
            {"parent_id": "page-parent", "title": "Board update draft"},
            make_ctx("notion", {"token": "t"}),
        )

        assert result.ok is True
        assert "Board update draft" in result.summary
        assert result.external_id == "page-new"
        assert result.revert_plan == {"action": "delete_page", "page_id": "page-new"}
        assert calls[0]["json"]["parent"] == {"page_id": "page-parent"}

    def test_delete_page_archives_and_reverts_by_unarchiving(self, monkeypatch):
        page = {
            "id": "page-aaa",
            "properties": {"title": {"type": "title", "title": [{"plain_text": "Old notes"}]}},
        }
        calls = stub_http(
            monkeypatch,
            __import__("duet.connectors.live.notion", fromlist=["x"]),
            [("PATCH", "/pages/page-aaa", StubResponse(200, page))],
        )
        adapter = NotionConnector()
        result = adapter.execute(
            "delete_page", {"page_id": "page-aaa"}, make_ctx("notion", {"token": "t"})
        )

        assert result.ok is True
        assert calls[0]["json"] == {"archived": True}
        assert result.revert_plan["archived"] is False
        assert result.revert_plan["action"] == "update_page"


# --------------------------------------------------------------------------- #
# Linear
# --------------------------------------------------------------------------- #
LINEAR_ISSUES = {
    "data": {
        "viewer": {
            "id": "user-me",
            "name": "Sam Owner",
            "email": "sam@yourcompany.com",
            "assignedIssues": {
                "nodes": [
                    {
                        "id": "issue-1",
                        "identifier": "ENG-204",
                        "title": "Connector retry loop times out",
                        "description": "Northwind sync fails on retry 3.",
                        "state": {"name": "Blocked", "type": "blocked"},
                        "priority": 1,
                        "dueDate": "2026-10-28",
                        "updatedAt": "2026-10-25T11:00:00.000Z",
                        "url": "https://linear.app/acme/issue/ENG-204",
                        "creator": {
                            "name": "Marcus",
                            "displayName": "marcus",
                            "email": "marcus@yourcompany.com",
                        },
                    },
                    {
                        "id": "issue-2",
                        "identifier": "ENG-210",
                        "title": "Tidy up the settings page",
                        "description": "",
                        "state": {"name": "In Progress", "type": "started"},
                        "priority": 3,
                        "dueDate": None,
                        "updatedAt": "2026-10-24T09:30:00.000Z",
                        "url": "https://linear.app/acme/issue/ENG-210",
                        "creator": {"name": "Lena", "displayName": "lena", "email": ""},
                    },
                ]
            },
        }
    }
}


class TestLinear:
    def test_no_credential_is_silent(self):
        adapter = LinearConnector()
        signals, cursor = adapter.fetch(make_ctx("linear", cursor={"updated_after": "z"}))
        assert signals == []
        assert cursor == {"updated_after": "z"}

    def test_fetch_maps_issues_and_their_hints(self, monkeypatch):
        stub_http(
            monkeypatch,
            __import__("duet.connectors.live.linear", fromlist=["x"]),
            [("POST", "linear.app/graphql", StubResponse(200, LINEAR_ISSUES))],
        )
        adapter = LinearConnector()
        signals, cursor = adapter.fetch(make_ctx("linear", {"api_key": "lin_api_xxx"}))

        assert len(signals) == 2
        assert {s.kind for s in signals} == {"ticket"}

        blocked, normal = signals
        assert blocked.external_id == "issue-1"
        assert blocked.title == "Connector retry loop times out"
        assert blocked.actor == "marcus@yourcompany.com"
        assert blocked.url == "https://linear.app/acme/issue/ENG-204"
        # A `blocked` state type means someone else is waiting.
        assert blocked.hints["blocker"] is True
        assert blocked.hints["deadline"] == "2026-10-28"
        # Priority 1 is Linear's "Urgent".
        assert blocked.hints["importance"] == "high"

        # An ordinary in-progress issue carries none of those.
        assert "blocker" not in normal.hints
        assert "deadline" not in normal.hints
        assert "importance" not in normal.hints
        assert normal.actor == "Lena"

        assert cursor["updated_after"] == "2026-10-25T11:00:00.000Z"

    def test_api_key_header_has_no_bearer_prefix(self, monkeypatch):
        """Linear 400s on `Bearer`, and the error reads like a bad query."""
        calls = stub_http(
            monkeypatch,
            __import__("duet.connectors.live.linear", fromlist=["x"]),
            [("POST", "linear.app/graphql", StubResponse(200, LINEAR_ISSUES))],
        )
        LinearConnector().fetch(make_ctx("linear", {"api_key": "lin_api_xxx"}))
        assert calls[0]["headers"]["Authorization"] == "lin_api_xxx"

    def test_graphql_auth_error_on_http_200_raises(self, monkeypatch):
        """GraphQL reports a dead key as a 200 with an `errors` array."""
        stub_http(
            monkeypatch,
            __import__("duet.connectors.live.linear", fromlist=["x"]),
            [
                (
                    "POST",
                    "linear.app/graphql",
                    StubResponse(200, {"errors": [{"message": "Authentication required"}]}),
                )
            ],
        )
        with pytest.raises(ConnectorAuthError):
            LinearConnector().fetch(make_ctx("linear", {"api_key": "revoked"}))

    def test_graphql_query_error_is_not_mistaken_for_silence(self, monkeypatch):
        stub_http(
            monkeypatch,
            __import__("duet.connectors.live.linear", fromlist=["x"]),
            [
                (
                    "POST",
                    "linear.app/graphql",
                    StubResponse(200, {"errors": [{"message": "Unknown field 'nope'"}]}),
                )
            ],
        )
        with pytest.raises(ConnectorError):
            LinearConnector().fetch(make_ctx("linear", {"api_key": "k"}))

    def test_create_issue_happy_path(self, monkeypatch):
        response = {
            "data": {
                "issueCreate": {
                    "success": True,
                    "issue": {
                        "id": "issue-new",
                        "identifier": "ENG-301",
                        "title": "Write the Northwind post-mortem",
                        "url": "https://linear.app/acme/issue/ENG-301",
                    },
                }
            }
        }
        calls = stub_http(
            monkeypatch,
            __import__("duet.connectors.live.linear", fromlist=["x"]),
            [("POST", "linear.app/graphql", StubResponse(200, response))],
        )
        result = LinearConnector().execute(
            "create_issue",
            {"title": "Write the Northwind post-mortem", "team_id": "team-eng", "priority": 2},
            make_ctx("linear", {"api_key": "k"}),
        )

        assert result.ok is True
        assert "ENG-301" in result.summary
        assert result.external_id == "issue-new"
        sent = calls[0]["json"]["variables"]["input"]
        assert sent["teamId"] == "team-eng"
        assert sent["priority"] == 2


# --------------------------------------------------------------------------- #
# Stripe
# --------------------------------------------------------------------------- #
def _stripe_routes(now: datetime) -> list[tuple[str, str, Any]]:
    due = int((now - timedelta(days=41)).timestamp())
    evidence_due = int((now + timedelta(days=5)).timestamp())
    return [
        (
            "GET",
            "/charges",
            StubResponse(
                200,
                {
                    "data": [
                        {
                            "id": "ch_failed_1",
                            "object": "charge",
                            # 1840000 cents == $18,400.00
                            "amount": 1840000,
                            "currency": "usd",
                            "status": "failed",
                            "created": int((now - timedelta(hours=3)).timestamp()),
                            "description": "Northwind annual renewal",
                            "failure_message": "Your card was declined.",
                            "failure_code": "card_declined",
                            "customer": "cus_northwind",
                            "receipt_email": "priya@northwind.io",
                        },
                        {
                            "id": "ch_ok_2",
                            "object": "charge",
                            "amount": 4900,
                            "currency": "usd",
                            "status": "succeeded",
                            "created": int((now - timedelta(hours=2)).timestamp()),
                        },
                    ]
                },
            ),
        ),
        (
            "GET",
            "/disputes",
            StubResponse(
                200,
                {
                    "data": [
                        {
                            "id": "dp_1",
                            "object": "dispute",
                            "amount": 250000,  # $2,500.00
                            "currency": "usd",
                            "status": "needs_response",
                            "reason": "product_not_received",
                            "charge": "ch_castle_9",
                            "created": int((now - timedelta(days=1)).timestamp()),
                            "evidence_details": {"due_by": evidence_due},
                        }
                    ]
                },
            ),
        ),
        (
            "GET",
            "/invoices",
            StubResponse(
                200,
                {
                    "data": [
                        {
                            "id": "in_late_1",
                            "object": "invoice",
                            "number": "2291",
                            "status": "open",
                            "amount_due": 1840000,
                            "amount_remaining": 1840000,
                            "currency": "usd",
                            "due_date": due,
                            "customer": "cus_castle",
                            "customer_email": "ap@castleretail.com",
                            "hosted_invoice_url": "https://invoice.stripe.com/i/2291",
                        },
                        {
                            "id": "in_future_2",
                            "object": "invoice",
                            "number": "2292",
                            "status": "open",
                            "amount_due": 50000,
                            "amount_remaining": 50000,
                            "currency": "usd",
                            "due_date": int((now + timedelta(days=10)).timestamp()),
                            "customer": "cus_other",
                        },
                    ]
                },
            ),
        ),
    ]


class TestStripe:
    def test_no_credential_is_silent(self):
        signals, cursor = StripeConnector().fetch(make_ctx("stripe", cursor={"a": 1}))
        assert signals == []
        assert cursor == {"a": 1}

    def test_fetch_covers_charges_disputes_and_overdue_invoices(self, monkeypatch):
        now = datetime.now(timezone.utc)
        stub_http(
            monkeypatch,
            __import__("duet.connectors.live.stripe", fromlist=["x"]),
            _stripe_routes(now),
        )
        signals, cursor = StripeConnector().fetch(make_ctx("stripe", {"secret_key": "sk_test"}))

        by_kind = {s.kind: s for s in signals}
        # The succeeded charge and the not-yet-due invoice are both omitted.
        assert sorted(by_kind) == ["dispute", "invoice_overdue", "payment_failed"]

        failed = by_kind["payment_failed"]
        assert failed.external_id == "ch_failed_1"
        assert failed.actor == "priya@northwind.io"
        # The whole point: cents -> dollars, once, before the engine sees it.
        assert failed.hints["amount"] == 18400.00
        assert failed.hints["customer"] is True
        assert failed.raw["amount_minor"] == 1840000

        dispute = by_kind["dispute"]
        assert dispute.hints["amount"] == 2500.00
        assert dispute.hints["escalation"] is True
        # Evidence deadline is a real, dated obligation.
        deadline = datetime.fromisoformat(dispute.hints["deadline"])
        assert 4 <= (deadline - now).days <= 5

        overdue = by_kind["invoice_overdue"]
        assert overdue.external_id == "in_late_1"
        assert overdue.hints["amount"] == 18400.00
        assert overdue.hints["days_overdue"] == 41
        assert "2291" in overdue.title
        assert cursor["last_fetch"]

    def test_amounts_never_reach_the_engine_in_cents(self, monkeypatch):
        """A missed division turns $184 into a $18,400 interrupt."""
        now = datetime.now(timezone.utc)
        stub_http(
            monkeypatch,
            __import__("duet.connectors.live.stripe", fromlist=["x"]),
            _stripe_routes(now),
        )
        signals, _ = StripeConnector().fetch(make_ctx("stripe", {"secret_key": "sk_test"}))
        for signal in signals:
            minor = signal.raw.get("amount_minor") or signal.raw.get(
                "amount_remaining_minor"
            )
            if minor:
                assert signal.hints["amount"] == minor / 100

    def test_transfer_balance_is_refused_without_calling_stripe(self, monkeypatch):
        """FORBIDDEN in the catalog. It must not be reachable by any path."""
        calls = stub_http(
            monkeypatch, __import__("duet.connectors.live.stripe", fromlist=["x"]), []
        )
        result = StripeConnector().execute(
            "transfer_balance",
            {"amount": 9800, "destination": "acct_elsewhere"},
            make_ctx("stripe", {"secret_key": "sk_test"}),
        )
        assert result.ok is False
        assert result.error == "forbidden"
        assert calls == []

    def test_transfer_balance_is_refused_even_with_no_credential(self, monkeypatch):
        calls = stub_http(
            monkeypatch, __import__("duet.connectors.live.stripe", fromlist=["x"]), []
        )
        result = StripeConnector().execute("transfer_balance", {}, make_ctx("stripe"))
        assert result.ok is False
        assert result.error == "forbidden"
        assert calls == []

    def test_refund_converts_dollars_to_minor_units(self, monkeypatch):
        refund = {
            "id": "re_1",
            "status": "succeeded",
            "amount": 250000,
            "currency": "usd",
        }
        calls = stub_http(
            monkeypatch,
            __import__("duet.connectors.live.stripe", fromlist=["x"]),
            [("POST", "/refunds", StubResponse(200, refund))],
        )
        result = StripeConnector().execute(
            "issue_refund",
            {"charge": "ch_castle_9", "amount": 2500},
            make_ctx("stripe", {"secret_key": "sk_test"}),
        )
        assert result.ok is True
        assert "2,500.00" in result.summary
        # Dollars in, cents on the wire.
        assert calls[0]["data"]["amount"] == 250000
        assert calls[0]["data"]["charge"] == "ch_castle_9"

    def test_refund_passes_minor_units_through_untouched(self, monkeypatch):
        calls = stub_http(
            monkeypatch,
            __import__("duet.connectors.live.stripe", fromlist=["x"]),
            [("POST", "/refunds", StubResponse(200, {"id": "re_2", "amount": 1999}))],
        )
        StripeConnector().execute(
            "issue_refund",
            {"charge": "ch_x", "amount_minor": 1999},
            make_ctx("stripe", {"secret_key": "sk_test"}),
        )
        assert calls[0]["data"]["amount"] == 1999

    def test_send_invoice_happy_path(self, monkeypatch):
        sent = {
            "id": "in_late_1",
            "number": "2291",
            "status": "open",
            "amount_due": 1840000,
            "currency": "usd",
        }
        calls = stub_http(
            monkeypatch,
            __import__("duet.connectors.live.stripe", fromlist=["x"]),
            [("POST", "/invoices/in_late_1/send", StubResponse(200, sent))],
        )
        result = StripeConnector().execute(
            "send_invoice", {"invoice_id": "in_late_1"}, make_ctx("stripe", {"secret_key": "sk"})
        )
        assert result.ok is True
        assert "2291" in result.summary
        assert "18,400.00" in result.summary
        assert calls[0]["url"].endswith("/invoices/in_late_1/send")

    def test_rejected_key_raises(self, monkeypatch):
        stub_http(
            monkeypatch,
            __import__("duet.connectors.live.stripe", fromlist=["x"]),
            [("GET", "/charges", StubResponse(401, {"error": {"message": "Invalid API Key"}}))],
        )
        with pytest.raises(ConnectorAuthError):
            StripeConnector().fetch(make_ctx("stripe", {"secret_key": "sk_bad"}))


# --------------------------------------------------------------------------- #
# Slack
# --------------------------------------------------------------------------- #
SLACK_AUTH = {
    "ok": True,
    "user_id": "U0ME",
    "user": "duet",
    "team": "Acme",
    "url": "https://acme.slack.com/",
}

SLACK_CHANNELS = {
    "ok": True,
    "channels": [
        {"id": "C_ENG", "name": "eng-oncall", "updated": 1790000002000},
        {"id": "D_LENA", "is_im": True, "user": "U_LENA", "updated": 1790000001000},
        {"id": "C_RANDOM", "name": "random", "updated": 1790000000000},
    ],
}

SLACK_HISTORY = {
    "C_ENG": {
        "ok": True,
        "messages": [
            {
                "type": "message",
                "user": "U_MARCUS",
                "text": "<@U0ME> the Northwind sync job is failing on retry 3",
                "ts": "1790000100.000100",
            },
            {
                "type": "message",
                "user": "U_SAM",
                "text": "deploy is green",
                "ts": "1790000090.000100",
            },
            {
                "type": "message",
                "user": "U0ME",
                "text": "<@U0ME> note to self",
                "ts": "1790000080.000100",
            },
            {"type": "message", "subtype": "channel_join", "user": "U_NEW", "ts": "1790000070.1"},
        ],
    },
    "D_LENA": {
        "ok": True,
        "messages": [
            {
                "type": "message",
                "user": "U_LENA",
                "text": "can you approve the contractor invoice before Friday?",
                "ts": "1790000050.000200",
            }
        ],
    },
    "C_RANDOM": {
        "ok": True,
        "messages": [
            {
                "type": "message",
                "user": "U_SAM",
                "text": "<!channel> lunch order going in at noon",
                "ts": "1790000040.000300",
            }
        ],
    },
}


def _slack_history(kwargs: dict[str, Any]) -> StubResponse:
    channel = kwargs.get("params", {}).get("channel")
    return StubResponse(200, SLACK_HISTORY.get(channel, {"ok": True, "messages": []}))


def _slack_routes() -> list[tuple[str, str, Any]]:
    return [
        ("GET", "auth.test", StubResponse(200, SLACK_AUTH)),
        ("GET", "conversations.list", StubResponse(200, SLACK_CHANNELS)),
        ("GET", "conversations.history", _slack_history),
    ]


class TestSlack:
    def test_no_credential_is_silent(self):
        signals, cursor = SlackConnector().fetch(make_ctx("slack", cursor={"user_id": "U0ME"}))
        assert signals == []
        assert cursor == {"user_id": "U0ME"}

    def test_fetch_keeps_only_what_is_addressed_to_you(self, monkeypatch):
        stub_http(
            monkeypatch,
            __import__("duet.connectors.live.slack", fromlist=["x"]),
            _slack_routes(),
        )
        signals, cursor = SlackConnector().fetch(make_ctx("slack", {"bot_token": "xoxb-1"}))

        by_id = {s.external_id: s for s in signals}
        # Ordinary chatter, our own message and a join event are all dropped.
        assert sorted(by_id) == [
            "C_ENG-1790000100.000100",
            "C_RANDOM-1790000040.000300",
            "D_LENA-1790000050.000200",
        ]

        mention = by_id["C_ENG-1790000100.000100"]
        assert mention.kind == "mention"
        # `<@U0ME>` is the only way a real mention appears on the wire.
        assert mention.hints["mention"] is True
        assert mention.actor == "U_MARCUS"
        assert "eng-oncall" in mention.title
        assert mention.url == "https://acme.slack.com/archives/C_ENG/p1790000100000100"

        dm = by_id["D_LENA-1790000050.000200"]
        assert dm.kind == "dm"
        assert dm.hints["dm"] is True

        broadcast = by_id["C_RANDOM-1790000040.000300"]
        # A room-wide shout is kept but marked, so noise_factor can discount it.
        assert broadcast.hints["channel_broadcast"] is True
        assert "mention" not in broadcast.hints

        assert cursor["user_id"] == "U0ME"
        assert cursor["oldest_ts"]

    def test_not_ok_on_http_200_is_an_error_not_silence(self, monkeypatch):
        """Slack's errors arrive with a 200, which would otherwise read as "quiet"."""
        stub_http(
            monkeypatch,
            __import__("duet.connectors.live.slack", fromlist=["x"]),
            [("GET", "auth.test", StubResponse(200, {"ok": False, "error": "invalid_auth"}))],
        )
        with pytest.raises(ConnectorAuthError):
            SlackConnector().fetch(make_ctx("slack", {"bot_token": "xoxb-dead"}))

    def test_non_auth_not_ok_also_raises(self, monkeypatch):
        stub_http(
            monkeypatch,
            __import__("duet.connectors.live.slack", fromlist=["x"]),
            [
                ("GET", "auth.test", StubResponse(200, SLACK_AUTH)),
                (
                    "GET",
                    "conversations.list",
                    StubResponse(200, {"ok": False, "error": "invalid_arguments"}),
                ),
            ],
        )
        with pytest.raises(ConnectorError):
            SlackConnector().fetch(make_ctx("slack", {"bot_token": "xoxb-1"}))

    def test_one_unreadable_channel_does_not_end_the_sweep(self, monkeypatch):
        def history(kwargs: dict[str, Any]) -> StubResponse:
            channel = kwargs.get("params", {}).get("channel")
            if channel == "C_ENG":
                return StubResponse(200, {"ok": False, "error": "not_in_channel"})
            return _slack_history(kwargs)

        stub_http(
            monkeypatch,
            __import__("duet.connectors.live.slack", fromlist=["x"]),
            [
                ("GET", "auth.test", StubResponse(200, SLACK_AUTH)),
                ("GET", "conversations.list", StubResponse(200, SLACK_CHANNELS)),
                ("GET", "conversations.history", history),
            ],
        )
        signals, _ = SlackConnector().fetch(make_ctx("slack", {"bot_token": "xoxb-1"}))
        # The DM and the broadcast still arrive.
        assert sorted(s.external_id for s in signals) == [
            "C_RANDOM-1790000040.000300",
            "D_LENA-1790000050.000200",
        ]

    def test_post_message_happy_path(self, monkeypatch):
        calls = stub_http(
            monkeypatch,
            __import__("duet.connectors.live.slack", fromlist=["x"]),
            [
                (
                    "POST",
                    "chat.postMessage",
                    StubResponse(200, {"ok": True, "channel": "C_ENG", "ts": "1790000200.1"}),
                )
            ],
        )
        result = SlackConnector().execute(
            "post_message",
            {"channel": "C_ENG", "text": "Shipping the retry fix now."},
            make_ctx("slack", {"bot_token": "xoxb-1"}),
        )
        assert result.ok is True
        assert "C_ENG" in result.summary
        assert result.external_id == "C_ENG-1790000200.1"
        assert calls[0]["json"]["text"] == "Shipping the retry fix now."

    def test_post_message_not_ok_is_a_failed_result(self, monkeypatch):
        stub_http(
            monkeypatch,
            __import__("duet.connectors.live.slack", fromlist=["x"]),
            [
                (
                    "POST",
                    "chat.postMessage",
                    StubResponse(200, {"ok": False, "error": "channel_not_found"}),
                )
            ],
        )
        result = SlackConnector().execute(
            "post_message",
            {"channel": "C_GONE", "text": "hello"},
            make_ctx("slack", {"bot_token": "xoxb-1"}),
        )
        assert result.ok is False
        assert "channel_not_found" in result.summary

    def test_set_status_records_the_previous_status_as_its_undo(self, monkeypatch):
        stub_http(
            monkeypatch,
            __import__("duet.connectors.live.slack", fromlist=["x"]),
            [
                (
                    "GET",
                    "users.profile.get",
                    StubResponse(
                        200,
                        {
                            "ok": True,
                            "profile": {"status_text": "Working", "status_emoji": ":computer:"},
                        },
                    ),
                ),
                (
                    "POST",
                    "users.profile.set",
                    StubResponse(
                        200, {"ok": True, "profile": {"status_text": "In a partner meeting"}}
                    ),
                ),
            ],
        )
        result = SlackConnector().execute(
            "set_status",
            {"status_text": "In a partner meeting", "status_emoji": ":calendar:"},
            make_ctx("slack", {"bot_token": "xoxb-1"}),
        )
        assert result.ok is True
        assert result.revert_plan == {
            "action": "set_status",
            "status_text": "Working",
            "status_emoji": ":computer:",
        }


# --------------------------------------------------------------------------- #
# IMAP
# --------------------------------------------------------------------------- #
PASSWORD = "app-password-never-logged"

# Built, not pasted, so the test proves the decode rather than a fixed blob.
SUBJECT_TEXT = "Rückfrage: Q3 résumé review – naïve café"
SUBJECT_ENCODED = "=?utf-8?B?" + base64.b64encode(SUBJECT_TEXT.encode("utf-8")).decode() + "?="

NEWSLETTER = b"""From: The Daily Standup <news@standupdaily.com>
To: sam@yourcompany.com
Subject: The Daily Standup: 7 AI tools you missed
Date: Sat, 24 Oct 2026 06:00:00 +0000
Message-ID: <news-9001@standupdaily.com>
List-Unsubscribe: <mailto:unsub@standupdaily.com>
Content-Type: text/plain; charset="utf-8"

Our picks for the week.
"""

URGENT = f"""From: Dana Lee <dana@harborventures.com>
To: sam@yourcompany.com
Subject: {SUBJECT_ENCODED}
Date: Sun, 25 Oct 2026 14:03:00 +0000
Message-ID: <harbor-7742@harborventures.com>
X-Priority: 1
Content-Type: text/plain; charset="utf-8"

Before Thursday we need the updated cohort retention numbers.
""".encode()

INTERNAL = b"""From: Lena Ortiz <lena@yourcompany.com>
To: sam@yourcompany.com
Subject: Contractor invoice
Date: Sun, 25 Oct 2026 09:00:00 +0000
Message-ID: <internal-55@yourcompany.com>
Content-Type: text/plain; charset="utf-8"

Needs approval before close.
"""

IMAP_MESSAGES = {7: NEWSLETTER, 9: URGENT, 11: INTERNAL}


class FakeIMAP:
    """Just enough imaplib to drive the adapter, recording how it was used."""

    last: FakeIMAP | None = None

    def __init__(self, host: str, port: int, login_error: str | None = None):
        self.host = host
        self.port = port
        self.login_error = login_error
        self.selected: tuple[str, bool] | None = None
        self.searched: tuple[Any, ...] = ()
        self.closed = False
        self.logged_out = False
        FakeIMAP.last = self

    def login(self, username: str, password: str) -> tuple[str, list[bytes]]:
        if self.login_error:
            raise imaplib.IMAP4.error(self.login_error)
        self.username = username
        self.password_seen = password
        return ("OK", [b"LOGIN completed"])

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.selected = (mailbox, readonly)
        return ("OK", [b"3"])

    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]:
        if command == "SEARCH":
            self.searched = args
            return ("OK", [b"7 9 11"])
        if command == "FETCH":
            uid = int(args[0])
            raw = IMAP_MESSAGES[uid]
            return ("OK", [(f"{uid} (RFC822 {{{len(raw)}}}".encode(), raw), b")"])
        raise AssertionError(f"unexpected uid command {command}")

    def close(self) -> None:
        self.closed = True

    def logout(self) -> None:
        self.logged_out = True


def stub_imap(monkeypatch: pytest.MonkeyPatch, *, login_error: str | None = None) -> None:
    module = __import__("duet.connectors.live.imap_mail", fromlist=["x"])
    monkeypatch.setattr(
        module,
        "imaplib",
        SimpleNamespace(
            IMAP4_SSL=lambda host, port: FakeIMAP(host, port, login_error),
            IMAP4=imaplib.IMAP4,
        ),
    )


def imap_credential() -> dict[str, Any]:
    return {
        "host": "imap.gmail.com",
        "port": 993,
        "username": "sam@yourcompany.com",
        "password": PASSWORD,
        "mailbox": "INBOX",
    }


class TestImap:
    def test_no_credential_is_silent(self):
        signals, cursor = ImapConnector().fetch(make_ctx("imap", cursor={"last_uid": 4}))
        assert signals == []
        assert cursor == {"last_uid": 4}

    def test_fetch_decodes_real_mail(self, monkeypatch):
        stub_imap(monkeypatch)
        signals, cursor = ImapConnector().fetch(make_ctx("imap", imap_credential()))

        assert len(signals) == 3
        assert {s.kind for s in signals} == {"email"}
        by_id = {s.external_id: s for s in signals}

        urgent = by_id["harbor-7742@harborventures.com"]
        # RFC2047: the encoded word must come back as real text, not mojibake.
        assert urgent.title == SUBJECT_TEXT
        assert "é" in urgent.title and "=?utf-8?B?" not in urgent.title
        # From is parsed to a bare address, not "Name <addr>".
        assert urgent.actor == "dana@harborventures.com"
        assert "cohort retention" in urgent.body
        assert urgent.hints["importance"] == "high"  # X-Priority: 1
        assert urgent.hints["external"] is True
        assert "bulk" not in urgent.hints

        bulk = by_id["news-9001@standupdaily.com"]
        # List-Unsubscribe is the one reliable marker of list mail.
        assert bulk.hints["bulk"] is True
        assert "importance" not in bulk.hints

        internal = by_id["internal-55@yourcompany.com"]
        # Same domain as the account, so not external.
        assert "external" not in internal.hints

        assert cursor["last_uid"] == 11

    def test_mailbox_is_opened_read_only(self, monkeypatch):
        """A sweep must not consume the unread flag it is reporting on."""
        stub_imap(monkeypatch)
        ImapConnector().fetch(make_ctx("imap", imap_credential()))
        assert FakeIMAP.last is not None
        assert FakeIMAP.last.selected == ("INBOX", True)

    def test_unseen_by_default_and_since_when_given(self, monkeypatch):
        stub_imap(monkeypatch)
        ImapConnector().fetch(make_ctx("imap", imap_credential()))
        assert FakeIMAP.last.searched == (None, "UNSEEN")

        stub_imap(monkeypatch)
        ImapConnector().fetch(
            make_ctx(
                "imap",
                imap_credential(),
                since=datetime(2026, 10, 24, tzinfo=timezone.utc),
            )
        )
        assert FakeIMAP.last.searched == (None, "SINCE", "24-Oct-2026")

    def test_uid_watermark_skips_what_was_already_reported(self, monkeypatch):
        stub_imap(monkeypatch)
        signals, cursor = ImapConnector().fetch(
            make_ctx("imap", imap_credential(), cursor={"last_uid": 9})
        )
        assert [s.external_id for s in signals] == ["internal-55@yourcompany.com"]
        assert cursor["last_uid"] == 11

    def test_password_never_appears_in_output(self, monkeypatch):
        stub_imap(monkeypatch)
        signals, cursor = ImapConnector().fetch(make_ctx("imap", imap_credential()))
        blob = repr(signals) + repr(cursor)
        assert PASSWORD not in blob

    def test_login_failure_raises_without_leaking_the_password(self, monkeypatch):
        stub_imap(monkeypatch, login_error="AUTHENTICATIONFAILED Invalid credentials")
        with pytest.raises(ConnectorAuthError) as caught:
            ImapConnector().fetch(make_ctx("imap", imap_credential()))
        message = str(caught.value)
        assert "sam@yourcompany.com" in message
        assert PASSWORD not in message

    def test_writes_are_not_supported(self):
        """IMAP is a read protocol; sending would mean SMTP and a second secret."""
        result = ImapConnector().execute(
            "send_email", {"to": "x@y.com"}, make_ctx("imap", imap_credential())
        )
        assert result.ok is False
        assert result.error == "not_implemented"


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
class TestRegistration:
    def test_adapters_are_live_not_demo(self, monkeypatch):
        """Registration has to be checked against a real `_register_live()` run.

        `conftest`'s scenario fixture registers scripted adapters over some
        keys for the rest of the suite. Re-running `_register_live()` into a
        patched table proves the real wiring without leaking anything into
        another test - monkeypatch restores both the table and the cache.
        """
        from duet.connectors import registry

        monkeypatch.setattr(registry, "REAL", {})
        monkeypatch.setattr(registry, "_cache", {})
        registry._register_live()

        for key, expected in (
            ("notion", NotionConnector),
            ("linear", LinearConnector),
            ("stripe", StripeConnector),
            ("slack", SlackConnector),
            ("imap", ImapConnector),
        ):
            assert registry.is_live(key), f"{key} is not registered as live"
            assert isinstance(registry.resolve(key), expected)
            assert registry.resolve(key).key == key

    def test_an_unknown_app_resolves_to_unavailable_not_a_fake(self):
        """The product must never substitute invented data for a missing app."""
        from duet.connectors import registry
        from duet.connectors.registry import UnavailableConnector

        adapter = registry.resolve("definitely_not_an_app")
        assert isinstance(adapter, UnavailableConnector)
        assert not registry.available("definitely_not_an_app")

    @pytest.mark.parametrize(
        ("key", "adapter_cls", "module_name"),
        [
            ("notion", NotionConnector, "duet.connectors.live.notion"),
            ("linear", LinearConnector, "duet.connectors.live.linear"),
            ("stripe", StripeConnector, "duet.connectors.live.stripe"),
            ("slack", SlackConnector, "duet.connectors.live.slack"),
        ],
    )
    def test_every_catalog_write_action_is_implemented(
        self, monkeypatch, key, adapter_cls, module_name
    ):
        """A verb the catalog declares but the adapter forgot is a silent dead end.

        The payloads are empty on purpose: each action should fail on its own
        missing fields, never fall through to BaseConnector's `not_implemented`.
        A credential is supplied so the dispatch is actually reached - without
        one every branch would short-circuit and this test would prove nothing,
        which is what `test_an_unknown_verb_is_not_implemented` guards.
        """
        from duet.connectors import catalog

        # Wildcard 200s: enough for a branch to run without reaching a network.
        stub_http(
            monkeypatch,
            __import__(module_name, fromlist=["x"]),
            [("GET", "", StubResponse(200, {})), ("POST", "", StubResponse(200, {}))],
        )
        spec = catalog.get(key)
        assert spec is not None
        assert spec.writes

        adapter = adapter_cls()
        ctx = make_ctx(key, {"token": "t", "api_key": "t", "secret_key": "t", "bot_token": "t"})
        for action in spec.writes:
            result = adapter.execute(action.key, {}, ctx)
            assert result.error != "not_implemented", f"{key}.{action.key} is unhandled"

    @pytest.mark.parametrize(
        "adapter_cls", [NotionConnector, LinearConnector, StripeConnector, SlackConnector]
    )
    def test_an_unknown_verb_is_not_implemented(self, adapter_cls):
        """The control for the test above: an unhandled action is distinguishable."""
        adapter = adapter_cls()
        ctx = make_ctx(
            adapter.key, {"token": "t", "api_key": "t", "secret_key": "t", "bot_token": "t"}
        )
        result = adapter.execute("wire_the_money_somewhere", {}, ctx)
        assert result.ok is False
        assert result.error == "not_implemented"
