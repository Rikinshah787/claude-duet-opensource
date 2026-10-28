"""Priority engine and the proactive sweep.

The engine's job is judgement, so the tests assert on *relative* ordering and on
the disposition boundaries rather than on exact scores - the weights are meant
to be tuned, and a test that pins them would make tuning impossible.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from duet.core.priority import (
    BRIEFING_THRESHOLD,
    NOTIFY_THRESHOLD,
    score_signal,
)
from duet.models import Disposition


def _in(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


class TestNoiseIsSuppressed:
    def test_newsletter_is_ignored(self):
        v = score_signal(
            kind="email",
            connector_key="gmail",
            title="7 AI tools you missed this week",
            actor="news@daily.com",
            hints={"bulk": True, "list_unsubscribe": True},
        )
        assert v.disposition is Disposition.IGNORE

    def test_channel_chatter_is_ignored(self):
        v = score_signal(
            kind="mention",
            connector_key="slack",
            title="@here lunch at noon",
            hints={"channel_broadcast": True, "social": True},
        )
        assert v.disposition is Disposition.IGNORE

    def test_receipt_does_not_reach_the_briefing(self):
        v = score_signal(
            kind="email",
            connector_key="gmail",
            title="Your receipt from Figma",
            hints={"bulk": True, "receipt": True},
        )
        assert v.score < BRIEFING_THRESHOLD


class TestConsequenceIsSurfaced:
    def test_large_failed_payment_interrupts(self):
        v = score_signal(
            kind="payment_failed",
            connector_key="stripe",
            title="Renewal failed",
            hints={"amount": 84000, "churn_risk": True},
        )
        assert v.disposition is Disposition.NOTIFY_NOW

    def test_money_scales_with_amount(self):
        def at(amount):
            return score_signal(
                kind="invoice_overdue",
                connector_key="quickbooks",
                title="Invoice overdue",
                hints={"amount": amount},
            ).score

        assert at(500) < at(5_000) < at(50_000)

    def test_imminent_deadline_outranks_distant_one(self):
        def at(hours):
            return score_signal(
                kind="deadline",
                connector_key="jira",
                title="SOC 2 evidence",
                hints={"deadline": _in(hours)},
            ).score

        assert at(2) > at(48) > at(400)

    def test_escalation_from_a_churn_risk_account_interrupts(self):
        v = score_signal(
            kind="escalation",
            connector_key="zendesk",
            title="Account raised ticket to urgent",
            hints={"escalation": True, "customer": True, "churn_risk": True},
        )
        assert v.disposition is Disposition.NOTIFY_NOW


class TestQuietHours:
    HINTS = {"amount": 84000, "churn_risk": True}

    def test_important_item_waits_until_morning(self):
        v = score_signal(
            kind="payment_failed",
            connector_key="stripe",
            title="Renewal failed",
            hints=self.HINTS,
            local_hour=2,
            quiet_hours={"start": 21, "end": 7},
        )
        assert v.disposition is Disposition.BRIEFING
        assert any("quiet hours" in r for r in v.reasons)

    def test_same_item_interrupts_during_the_day(self):
        v = score_signal(
            kind="payment_failed",
            connector_key="stripe",
            title="Renewal failed",
            hints=self.HINTS,
            local_hour=14,
            quiet_hours={"start": 21, "end": 7},
        )
        assert v.disposition is Disposition.NOTIFY_NOW

    def test_a_true_emergency_still_breaks_through(self):
        v = score_signal(
            kind="security_alert",
            connector_key="okta",
            title="Admin credentials used from an unrecognised location",
            hints={
                "importance": "high",
                "escalation": True,
                "amount": 100000,
                "deadline": _in(1),
                "sensitive": True,
            },
            local_hour=3,
            quiet_hours={"start": 21, "end": 7},
        )
        assert v.disposition is Disposition.NOTIFY_NOW

    @pytest.mark.parametrize(
        "hour,expected",
        [(22, True), (2, True), (6, True), (7, False), (12, False), (20, False)],
    )
    def test_window_wraps_midnight(self, hour, expected):
        from duet.core.priority import _in_quiet_hours

        assert _in_quiet_hours(hour, {"start": 21, "end": 7}) is expected


class TestUserRules:
    def test_mute_suppresses_an_otherwise_notable_item(self):
        class Rule:
            kind, pattern, weight, enabled = "mute", "standupdaily", 1.0, True

        v = score_signal(
            kind="vip_email",
            connector_key="gmail",
            title="Big news from standupdaily",
            actor="news@standupdaily.com",
            hints={"vip": True},
            rules=[Rule()],
        )
        assert v.disposition in {Disposition.IGNORE, Disposition.DEFER}

    def test_vip_list_lifts_an_ordinary_email(self):
        plain = score_signal(
            kind="email", connector_key="gmail", title="Quick question",
            actor="dana@harborventures.com",
        )
        lifted = score_signal(
            kind="email", connector_key="gmail", title="Quick question",
            actor="dana@harborventures.com", vips={"harborventures.com"},
        )
        assert lifted.score > plain.score


class TestLearning:
    def test_repeatedly_dismissed_category_is_quieted(self):
        history = {"deal_stale": {"handled": 0, "dismissed": 9}}
        with_history = score_signal(
            kind="deal_stale", connector_key="hubspot", title="Deal idle",
            hints={"amount": 40000}, history=history,
        )
        without = score_signal(
            kind="deal_stale", connector_key="hubspot", title="Deal idle",
            hints={"amount": 40000},
        )
        assert with_history.score < without.score

    def test_acted_on_category_is_sharpened(self):
        history = {"invoice_overdue": {"handled": 8, "dismissed": 1}}
        boosted = score_signal(
            kind="invoice_overdue", connector_key="quickbooks", title="Overdue",
            hints={"amount": 5000}, history=history,
        )
        plain = score_signal(
            kind="invoice_overdue", connector_key="quickbooks", title="Overdue",
            hints={"amount": 5000},
        )
        assert boosted.score > plain.score

    def test_thin_history_is_not_acted_on(self):
        """Two data points is not a pattern."""
        history = {"email": {"handled": 0, "dismissed": 2}}
        assert (
            score_signal(kind="email", connector_key="gmail", title="Hi", history=history).score
            == score_signal(kind="email", connector_key="gmail", title="Hi").score
        )


class TestExplainability:
    def test_every_verdict_carries_its_reasoning(self):
        v = score_signal(
            kind="invoice_overdue",
            connector_key="quickbooks",
            title="Invoice overdue",
            hints={"amount": 18400, "days_overdue": 41},
        )
        assert v.reasons
        assert any("18,400" in r for r in v.reasons)
        assert any("41 days overdue" in r for r in v.reasons)

    def test_factors_are_itemised(self):
        v = score_signal(kind="email", connector_key="gmail", title="Hello")
        names = {f.name for f in v.factors}
        assert {"kind", "urgency", "financial", "relationship", "importance", "noise"} <= names

    def test_score_is_bounded(self):
        v = score_signal(
            kind="security_alert",
            connector_key="okta",
            title="Everything at once",
            hints={
                "amount": 10_000_000, "importance": "high", "escalation": True,
                "blocker": True, "conflict": True, "churn_risk": True,
                "deadline": _in(1), "days_overdue": 500, "vip": True,
            },
        )
        assert 0 <= v.score <= 100


class TestSweep:
    def test_sweep_is_idempotent(self, seeded):
        """Re-polling the same source must not duplicate signals."""
        before = len(seeded.get("/api/signals?limit=400").json()["signals"])
        seeded.post("/api/sync")
        seeded.post("/api/sync")
        after = len(seeded.get("/api/signals?limit=400").json()["signals"])
        assert before == after

    def test_sweep_produces_a_spread_not_all_urgent(self, seeded):
        """If everything is urgent, nothing is."""
        signals = seeded.get("/api/signals?limit=400").json()["signals"]
        urgent = [s for s in signals if s["disposition"] == "notify_now"]
        assert signals
        assert len(urgent) / len(signals) < 0.4

    def test_sweep_never_writes_to_a_connected_app(self, seeded):
        """The sweep is read-only: it must not create approvals or execute."""
        seeded.post("/api/sync")
        actions = [
            e["action"] for e in seeded.get("/api/audit?limit=200").json()["entries"]
        ]
        assert "action.executed" not in actions

    def test_errored_connector_recovers_on_the_next_sweep(self, seeded, db):
        from sqlalchemy import select

        from duet.models import Connection

        conn = db.execute(
            select(Connection).where(Connection.connector_key == "gmail")
        ).scalar_one()
        conn.status = "error"
        conn.last_error = "transient"
        db.commit()

        seeded.post("/api/sync")
        db.refresh(conn)
        assert conn.status == "connected"


class TestAgeDirectionDependsOnWhoIsWaiting:
    """Found against real GitHub data: 28 live notifications all scored `ignore`.

    The stale-news ones deserved it. But the engine applied the same flat
    recency decay to items where a *person was blocked on the user*, which
    buried a month-old review request below a fresh newsletter. Age means the
    opposite thing in those two cases.
    """

    def _at(self, kind: str, days: float, **hints):
        from datetime import datetime, timedelta, timezone

        return score_signal(
            kind=kind,
            connector_key="github",
            title="x",
            occurred_at=datetime.now(timezone.utc) - timedelta(days=days),
            hints=hints,
        ).score

    @pytest.mark.parametrize(
        "kind", ["review_request", "thread_waiting", "signature_pending", "invoice_overdue"]
    )
    def test_waiting_on_you_gets_worse_with_age(self, kind):
        assert self._at(kind, 30) > self._at(kind, 7) > self._at(kind, 0)

    @pytest.mark.parametrize("kind", ["email", "doc_update", "campaign_result"])
    def test_inbound_news_decays_with_age(self, kind):
        assert self._at(kind, 0) > self._at(kind, 30)

    def test_ageing_in_is_capped(self):
        """A years-old thread must not outrank a live crisis."""
        ancient = self._at("thread_waiting", 900)
        crisis = score_signal(
            kind="payment_failed",
            connector_key="stripe",
            title="Renewal failed",
            hints={"amount": 84000, "churn_risk": True},
        ).score
        assert ancient < crisis

    def test_the_reason_says_how_long(self):
        from datetime import datetime, timedelta, timezone

        v = score_signal(
            kind="review_request",
            connector_key="github",
            title="PR waiting",
            occurred_at=datetime.now(timezone.utc) - timedelta(days=28),
        )
        assert any("waiting" in r for r in v.reasons)

    def test_a_fresh_escalation_keeps_its_urgency(self):
        """Regression: making old items age *in* must not de-escalate new ones.

        `escalation` is a waiting-on-you kind, and routing it down the ageing-in
        branch dropped the freshness bonus, quietly pushing a live customer
        incident below the notify threshold.
        """
        v = score_signal(
            kind="escalation",
            connector_key="zendesk",
            title="Account raised ticket to urgent",
            hints={"escalation": True, "customer": True, "churn_risk": True},
        )
        assert v.disposition is Disposition.NOTIFY_NOW
