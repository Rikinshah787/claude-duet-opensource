"""The conversation itself: greetings, status questions, and what becomes a task.

Every test here pins a bug that shipped:

* "hi" answered "Morning." at five in the afternoon, because `User.timezone`
  defaults to "UTC" and that default was honoured as though someone had chosen
  it.
* The greeting was a fixed sentence. It told the user to say "what needs me?"
  and said nothing about the forty emails it had just read.
* "what needs me?" - the exact phrase the greeting advertises - matched nothing
  and spawned a full agent task, which is the slowest possible way to answer a
  question the database already knows.
* The status pattern used `.search`, so "what do I need to send to Priya"
  matched on "what do i need" and came back as a status summary instead of
  being done.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from duet.api.thread import (
    CONNECTION_QUERY,
    CONNECTIONS_OVERVIEW,
    CONVERSATIONAL,
    STATUS_QUERY,
    _mentioned_connectors,
    _part_of_day,
)


def route(text: str) -> str:
    """Where a message goes, by the same rules `post_thread` applies."""
    if CONNECTIONS_OVERVIEW.match(text):
        return "overview"
    if CONNECTION_QUERY.match(text):
        specific, generic = _mentioned_connectors(text)
        if specific or generic:
            return "connection"
    if STATUS_QUERY.match(text):
        return "status"
    if CONVERSATIONAL.match(text):
        return "greeting"
    return "task"


# --------------------------------------------------------------------------- #
# routing
# --------------------------------------------------------------------------- #
class TestRouting:
    @pytest.mark.parametrize(
        "text",
        [
            "what needs me?",
            "what needs me",
            "What needs my attention",
            "whats up",
            "what's new",
            "anything urgent",
            "anything for me?",
            "catch me up",
            "what should i look at",
            "status",
            "hey, what needs me?",
        ],
    )
    def test_status_questions_are_answered_inline(self, text):
        """None of these should cost an agent run - the answer is already stored."""
        assert route(text) == "status"

    @pytest.mark.parametrize("text", ["hi", "hello", "Hey!", "thanks", "ok", "sure"])
    def test_greetings_are_answered_inline(self, text):
        assert route(text) == "greeting"

    @pytest.mark.parametrize(
        "text",
        [
            "what do i need to send to Priya",
            "draft a reply to the figma email",
            "chase overdue invoices",
            "what should i look at in the Northwind contract",
            "catch me up on the Series A and then email Dana",
        ],
    )
    def test_real_requests_still_become_tasks(self, text):
        """The status pattern must not swallow work that happens to start the same way."""
        assert route(text) == "task"

    def test_a_status_question_wins_over_the_greeting_pattern(self):
        """"what's up" reads as a greeting but is a question, and "Morning." is no answer."""
        assert route("whats up") == "status"


# --------------------------------------------------------------------------- #
# time of day
# --------------------------------------------------------------------------- #
class TestPartOfDay:
    @pytest.mark.parametrize(
        "hour,expected",
        [(5, "Morning"), (8, "Morning"), (11, "Morning"),
         (12, "Afternoon"), (16, "Afternoon"),
         (17, "Evening"), (21, "Evening"),
         (22, "Late one"), (2, "Late one"), (4, "Late one")],
    )
    def test_the_greeting_matches_the_clock(self, hour, expected):
        assert _part_of_day(datetime(2026, 10, 26, hour)) == expected

    def test_five_pm_is_not_morning(self):
        """The reported bug, as a test."""
        assert _part_of_day(datetime(2026, 10, 26, 17, 13)) != "Morning"


class TestLocalNow:
    def test_an_unset_timezone_falls_back_to_the_machine_clock(self):
        """"UTC" is the column default, not a choice, and this runs on the user's own machine."""
        from types import SimpleNamespace

        from duet.api.thread import _local_now

        now = _local_now(SimpleNamespace(timezone="UTC"))
        assert now.utcoffset() == datetime.now().astimezone().utcoffset()

    def test_a_real_timezone_is_honoured(self):
        from types import SimpleNamespace

        from duet.api.thread import _local_now

        now = _local_now(SimpleNamespace(timezone="Asia/Tokyo"))
        assert now.utcoffset() == timedelta(hours=9)

    def test_a_nonsense_timezone_does_not_break_a_greeting(self):
        from types import SimpleNamespace

        from duet.api.thread import _local_now

        assert _local_now(SimpleNamespace(timezone="Mars/Olympus_Mons")) is not None


# --------------------------------------------------------------------------- #
# what the greeting actually says
# --------------------------------------------------------------------------- #
class TestGreetingIsGrounded:
    """The back half of a greeting is real state, in priority order.

    What is waiting beats what was read, because "2 things want you now" is the
    sentence worth spending a greeting on.
    """

    def _say(self, client, text: str) -> dict:
        return client.post("/api/thread", json={"text": text}).json()

    def _signal(self, db, user, *, disposition, title="Something happened", hours_ago=1):
        from duet.models import Signal

        now = datetime.now(timezone.utc)
        row = Signal(
            user_id=user.id,
            connector_key="gmail",
            external_id=f"sig-{title}-{hours_ago}",
            kind="email",
            title=title,
            actor="someone@example.com",
            occurred_at=now - timedelta(hours=hours_ago),
            disposition=disposition,
            state="new",
            score=90.0,
        )
        db.add(row)
        db.commit()
        return row

    # ----------------------------------------------------------- priority --
    def test_urgent_signals_lead(self, db, user):
        from duet.api.thread import _standing
        from duet.models import Disposition

        self._signal(db, user, disposition=Disposition.NOTIFY_NOW.value)
        assert "wants you now" in _standing(db, user)

    def test_pending_approvals_come_next(self, db, user):
        from duet.api.thread import _standing
        from duet.models import Approval, ApprovalStatus

        db.add(
            Approval(
                user_id=user.id,
                title="Send the reminder to Dana",
                connector_key="gmail",
                action_type="send_email",
                payload={},
                status=ApprovalStatus.PENDING.value,
            )
        )
        db.commit()
        assert "approval" in _standing(db, user).lower()

    def test_otherwise_it_says_what_it_read(self, db, user):
        """The useful half of "nothing needs you" is "and here is what I looked at"."""
        from duet.api.thread import _standing
        from duet.models import Disposition

        for i in range(3):
            self._signal(
                db, user, disposition=Disposition.IGNORE.value,
                title=f"Newsletter {i}", hours_ago=i + 1,
            )
        standing = _standing(db, user)
        assert "read 3 things" in standing

    def test_an_empty_account_says_so_plainly(self, db, user):
        from duet.api.thread import _standing

        assert "Nothing has come in yet" in _standing(db, user)

    def test_the_count_is_what_was_read_not_when_it_was_sent(self, db, user):
        """A week-old email read today counts; it is news to the user either way."""
        from duet.api.thread import _standing
        from duet.models import Disposition

        self._signal(
            db, user, disposition=Disposition.IGNORE.value,
            title="Sent last week, read just now", hours_ago=168,
        )
        assert "read 1 things" in _standing(db, user)

    def test_what_was_read_last_week_is_not_todays_news(self, db, user):
        from duet.api.thread import _standing
        from duet.models import Disposition, Signal

        row = self._signal(db, user, disposition=Disposition.IGNORE.value, title="Old")
        # Ingested two days ago, so it is no longer part of "the last day".
        row.created_at = datetime.now(timezone.utc) - timedelta(hours=48)
        db.commit()

        assert "Nothing has come in yet" in _standing(db, user)

    # -------------------------------------------------------- end to end --
    def test_hi_is_answered_without_spawning_a_task(self, client):
        body = self._say(client, "hi")
        assert body["task"] is None
        assert body["reply"]["content"]

    def test_hi_is_grounded_on_a_real_account(self, seeded):
        """The seeded world has something urgent, so the greeting must say so."""
        content = self._say(seeded, "hi")["reply"]["content"]
        assert "wants you now" in content or "want you now" in content

    def test_the_greeting_is_not_the_old_fixed_sentence(self, client):
        content = self._say(client, "hi")["reply"]["content"]
        assert content != (
            "Morning. Ask me for anything, or say 'what needs me?' for the short version."
        )

    def test_what_needs_me_is_answered_inline(self, seeded):
        """The phrase the product suggests must not cost an agent run."""
        body = self._say(seeded, "what needs me?")
        assert body["task"] is None
        assert body["reply"]["content"]

    def test_thanks_still_gets_out_of_the_way(self, client):
        assert self._say(client, "thanks")["reply"]["content"] == "Any time."


# --------------------------------------------------------------------------- #
# "is it connected?"
# --------------------------------------------------------------------------- #
class TestConnectionQuestions:
    """Connection state comes from the connections table, never from a model.

    The bug this pins: asked "is email connected?", the assistant spawned an
    agent task, the research agent keyword-matched the word "email" against
    stored messages, and an LLM concluded "yes, Gmail is connected" from the
    fact that emails existed. Stored messages outlive a revoked grant, so that
    answer is wrong for days. Asked about an app with no stored messages it was
    worse than wrong: it described the connectors of the machine running the
    model as though they were the user's, and sent them to a different
    product's settings page.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "is email connected ?",
            "is app connected gmail",   # the user's own phrasing: app after the verb
            "is gmail connected",
            "is slack connected?",
            "am i connected to gmail",
            "is my email hooked up",
            "is my drive linked",
            "hey, is gmail connected?",
            "did gmail connect",
        ],
    )
    def test_questions_about_one_app_are_answered_inline(self, text):
        assert route(text) == "connection"

    @pytest.mark.parametrize(
        "text",
        [
            "what apps are connected",
            "which apps are connected?",
            "are my apps connected",
            "whats connected",
            "connection status",
            "show my connected apps",
            "is everything connected",
            "list my connections",
        ],
    )
    def test_questions_about_all_apps_list_them(self, text):
        assert route(text) == "overview"

    @pytest.mark.parametrize(
        "text",
        [
            "are the invoices linked",
            "is the northwind contract linked to the invoice",
            # Names two real connectors and is still about the user's documents.
            "is the figma file linked to the notion page",
            "is sarah connected to the acme deal",
            "connect gmail to my calendar and sync them",
            "link the invoice to the contract",
            "is the deploy connected to staging",
        ],
    )
    def test_work_that_merely_sounds_like_one_stays_a_task(self, text):
        """A trailing "to <something>" joins two things; it does not name an app."""
        assert route(text) == "task"

    # ------------------------------------------------------------ resolution --
    def test_a_named_app_wins_over_an_implied_one(self):
        specific, generic = _mentioned_connectors("is my gmail connected")
        assert specific == ["gmail"]

    def test_a_generic_word_implies_every_app_it_could_mean(self):
        _, generic = _mentioned_connectors("is email connected")
        assert set(generic) == {"gmail", "outlook", "imap"}

    def test_the_longest_name_wins(self):
        """"google calendar" must not be reduced to the "calendar" alias."""
        specific, _ = _mentioned_connectors("is google calendar connected")
        assert specific == ["google_calendar"]

    # --------------------------------------------------------- the sentences --
    def _one(self, **kw):
        from types import SimpleNamespace

        from duet.api.thread import _one_connection

        row = SimpleNamespace(
            credential_ref=kw.get("credential_ref", "ref"),
            status=kw.get("status", "connected"),
            enabled=kw.get("enabled", True),
            account_label=kw.get("account_label", ""),
            last_sync_at=kw.get("last_sync_at"),
            last_error=kw.get("last_error"),
        )
        return _one_connection("gmail", row, kw.get("count", 0))

    def test_a_credential_is_what_makes_it_connected(self):
        assert self._one().startswith("Yes - Gmail is connected")

    def test_a_revoked_row_is_not_connected(self):
        """Rows are kept for the audit trail, so presence is not connectedness."""
        assert self._one(credential_ref=None).startswith("No - Gmail is not connected")

    def test_a_failed_connection_says_what_failed(self):
        out = self._one(status="error", last_error="Google rejected the token")
        assert "not working" in out and "Google rejected the token" in out

    def test_a_disabled_connection_is_not_silently_reported_as_fine(self):
        assert "switched off" in self._one(enabled=False)

    def test_an_app_with_no_adapter_says_so_rather_than_offering_setup(self):
        """Pointing someone at a sign-in button that leads nowhere is worse than "no"."""
        from duet.api.thread import _one_connection

        out = _one_connection("linkedin", None, 0)
        assert "no working integration" in out

    def test_an_unconnected_app_is_pointed_at_this_products_own_panel(self):
        from duet.api.thread import _one_connection

        out = _one_connection("slack", None, 0)
        assert "Open Apps" in out
        # The bug: the model answered this with another product's settings page.
        assert "claude.ai" not in out.lower()

    def test_the_answer_never_depends_on_stored_messages(self):
        """82 emails on file say nothing about whether the grant still works."""
        assert self._one(credential_ref=None, count=82).startswith("No - ")

    # -------------------------------------------------------------- end to end --
    def test_asking_costs_no_task_and_no_model_call(self, client):
        body = client.post("/api/thread", json={"text": "is gmail connected?"}).json()
        assert body["task"] is None
        assert body["reply"]["meta"]["answered"] == "connections"

    def test_an_empty_account_says_nothing_is_connected(self, client):
        body = client.post("/api/thread", json={"text": "what apps are connected?"}).json()
        assert "Nothing is connected yet" in body["reply"]["content"]
