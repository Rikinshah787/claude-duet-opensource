"""The priority engine.

Decides, for every observation, whether to interrupt you, save it for the
morning brief, sit on it, or drop it.

Two properties matter more than the exact numbers:

1. **It is explainable.** Every factor returns points *and* a sentence. The
   score is the sum; the sentences are what the UI shows when you ask "why did
   you tell me about this?". A black-box score would be impossible to tune and
   impossible to trust.

2. **Interrupting is expensive.** The default is the briefing, not the
   notification. A signal has to clear a high bar to break into someone's day,
   and quiet hours raise that bar further - only a genuine emergency gets
   through at 02:00.

The weights below are a starting policy, not a claim about any individual. They
are meant to be tuned per user from `PolicyRule` rows and from observed
behaviour (see `historical_factor`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..models import Disposition

# Thresholds on the 0-100 scale.
NOTIFY_THRESHOLD = 72.0
BRIEFING_THRESHOLD = 40.0
DEFER_THRESHOLD = 18.0
# During quiet hours only this clears the bar.
EMERGENCY_THRESHOLD = 88.0


@dataclass
class Factor:
    name: str
    points: float
    reason: str = ""


@dataclass
class Verdict:
    score: float
    disposition: Disposition
    factors: list[Factor] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "disposition": self.disposition.value,
            "factors": {f.name: round(f.points, 1) for f in self.factors},
            "reasons": self.reasons,
        }


# --------------------------------------------------------------------------- #
# Base weights by signal kind: how much this *type* of thing usually matters.
# --------------------------------------------------------------------------- #
KIND_WEIGHTS: dict[str, float] = {
    # money
    "payment_failed": 30,
    "dispute": 30,
    "invoice_overdue": 24,
    "cash_risk": 34,
    "payroll_due": 30,
    "revenue_risk": 32,
    # customers
    "escalation": 30,
    "customer_message": 18,
    "deal_risk": 26,
    "deal_stale": 16,
    "sla_breach": 28,
    # time
    "conflict": 28,
    "meeting": 12,
    "prep_needed": 20,
    "deadline": 24,
    "signature_pending": 20,
    "task_due": 12,
    # work
    "blocker": 20,
    "review_request": 14,
    "ci_failure": 14,
    "incident": 32,
    "ticket": 10,
    # comms
    "vip_email": 22,
    "dm": 14,
    "mention": 12,
    "email": 8,
    "thread_waiting": 14,
    # ops
    "inventory_low": 16,
    "order": 6,
    "doc_update": 5,
    "share_request": 14,
    "security_alert": 34,
    "credential_expiry": 18,
    "automation_failed": 12,
    "metric_anomaly": 18,
    "campaign_result": 6,
    "booking": 8,
    "recording_ready": 4,
    "contact_activity": 6,
    "bill_due": 20,
}

# Connectors whose events are, on average, more consequential.
CONNECTOR_BIAS: dict[str, float] = {
    "stripe": 1.15,
    "quickbooks": 1.15,
    "gusto": 1.1,
    "docusign": 1.05,
    "zendesk": 1.05,
    "intercom": 1.05,
    "okta": 1.2,
    "sentry": 1.05,
    "mailchimp": 0.85,
    "tiktok": 0.7,
    "canva": 0.7,
    "miro": 0.75,
}


def kind_factor(kind: str, connector_key: str) -> Factor:
    base = KIND_WEIGHTS.get(kind, 8.0)
    bias = CONNECTOR_BIAS.get(connector_key, 1.0)
    pts = base * bias
    label = kind.replace("_", " ")
    return Factor("kind", pts, f"{label} from {connector_key}")


# Kinds where someone else is blocked until you act. These age *into*
# importance rather than out of it: a review request that has been sitting for a
# month is a worse problem than one from this morning, not a staler one. Applying
# a flat recency decay to these was wrong - it buried exactly the items a
# chief-of-staff exists to catch.
WAITING_ON_YOU = frozenset(
    {
        "review_request",
        "thread_waiting",
        "signature_pending",
        "share_request",
        "dm",
        "escalation",
        "blocker",
        "deal_stale",
        "invoice_overdue",
    }
)


def urgency_factor(occurred_at: datetime, hints: dict, kind: str = "") -> Factor:
    """Deadline proximity dominates; recency is a weak secondary signal.

    Direction of the age term depends on who is waiting. For inbound news, old
    means less relevant. For anything where a person is blocked on the user, old
    means more overdue.
    """
    now = datetime.now(timezone.utc)
    pts = 0.0
    reasons: list[str] = []

    deadline_raw = hints.get("deadline")
    if deadline_raw:
        try:
            deadline = datetime.fromisoformat(str(deadline_raw))
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            hours_left = (deadline - now).total_seconds() / 3600
            if hours_left < 0:
                pts += 26
                reasons.append(f"deadline passed {abs(int(hours_left))}h ago")
            elif hours_left <= 4:
                pts += 28
                reasons.append(f"due in {int(hours_left)}h")
            elif hours_left <= 24:
                pts += 22
                reasons.append("due within a day")
            elif hours_left <= 72:
                pts += 14
                reasons.append("due within three days")
            else:
                pts += 5
                reasons.append("has a deadline")
        except (ValueError, TypeError):
            pass

    age_h = (now - occurred_at).total_seconds() / 3600

    # Freshness: something that just happened is worth a look, whatever it is.
    freshness = 6.0 if age_h <= 1 else (3.0 if age_h <= 6 else 0.0)

    if kind in WAITING_ON_YOU:
        # Both ends of the curve matter here, so take whichever is stronger
        # rather than replacing one with the other: a customer escalation is
        # urgent in its first hour *and* damning after three weeks. Dropping
        # the freshness term entirely silently de-escalated live incidents.
        overdue = 0.0
        note = ""
        if age_h > 24 * 14:
            overdue, note = 14.0, f"someone has been waiting {int(age_h / 24)} days"
        elif age_h > 24 * 3:
            overdue, note = 9.0, f"waiting on you for {int(age_h / 24)} days"
        elif age_h > 24:
            overdue, note = 4.0, "waiting on you since yesterday"

        pts += max(freshness, overdue)
        if overdue >= freshness and note:
            reasons.append(note)
    else:
        pts += freshness
        if age_h > 72:
            pts -= 4
            reasons.append("more than three days old")

    if hints.get("days_overdue"):
        days = int(hints["days_overdue"])
        pts += min(18, days * 0.4)
        reasons.append(f"{days} days overdue")

    return Factor("urgency", pts, "; ".join(reasons))


def financial_factor(hints: dict) -> Factor:
    """Log-ish scaling: $100 and $100k should not be one point apart."""
    amount = hints.get("amount")
    if not amount:
        return Factor("financial", 0.0, "")
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return Factor("financial", 0.0, "")

    if value >= 50_000:
        pts, note = 26.0, f"${value:,.0f} at stake"
    elif value >= 20_000:
        pts, note = 20.0, f"${value:,.0f} at stake"
    elif value >= 5_000:
        pts, note = 13.0, f"${value:,.0f} at stake"
    elif value >= 1_000:
        pts, note = 8.0, f"${value:,.0f} at stake"
    else:
        pts, note = 3.0, f"${value:,.0f} at stake"
    return Factor("financial", pts, note)


def relationship_factor(actor: str, hints: dict, vips: set[str]) -> Factor:
    """Who it is from. VIP lists are explicit; customer/churn flags are inferred."""
    pts = 0.0
    reasons: list[str] = []
    actor_l = (actor or "").lower()

    if actor_l and any(v in actor_l for v in vips):
        pts += 20
        reasons.append("from someone on your VIP list")
    elif hints.get("vip"):
        pts += 16
        reasons.append("from a key contact")

    if hints.get("customer"):
        pts += 10
        reasons.append("from a customer")
    if hints.get("churn_risk"):
        pts += 14
        reasons.append("account is a churn risk")
    if hints.get("external"):
        pts += 4
    if hints.get("sentiment") == "negative":
        pts += 8
        reasons.append("tone is negative")

    return Factor("relationship", pts, "; ".join(reasons))


def importance_factor(hints: dict) -> Factor:
    pts = 0.0
    reasons: list[str] = []
    if hints.get("importance") == "high":
        pts += 12
        reasons.append("flagged high importance")
    if hints.get("blocker"):
        pts += 10
        reasons.append("blocking someone else")
    if hints.get("escalation"):
        pts += 12
        reasons.append("already escalated")
    if hints.get("conflict"):
        pts += 10
        reasons.append("creates a scheduling conflict")
    if hints.get("prep_needed"):
        pts += 6
    if hints.get("persistent"):
        pts += 6
        reasons.append("ongoing, not a one-off")
    if hints.get("legal") or hints.get("sensitive"):
        pts += 8
        reasons.append("has legal or privacy weight")
    if hints.get("links_to"):
        pts += 5
        reasons.append("connected to something else already open")
    return Factor("importance", pts, "; ".join(reasons))


def noise_factor(hints: dict) -> Factor:
    """Subtracts. Bulk mail and chatter should never reach a notification."""
    pts = 0.0
    reasons: list[str] = []
    if hints.get("bulk") or hints.get("list_unsubscribe"):
        pts -= 22
        reasons.append("bulk mail")
    if hints.get("receipt"):
        pts -= 10
        reasons.append("routine receipt")
    if hints.get("social"):
        pts -= 14
        reasons.append("social chatter")
    if hints.get("channel_broadcast") and not hints.get("mention"):
        pts -= 10
        reasons.append("broadcast, not addressed to you")
    if hints.get("automated"):
        pts -= 6
    return Factor("noise", pts, "; ".join(reasons))


def preference_factor(signal_text: str, connector_key: str, rules: list) -> Factor:
    """User-authored rules. These are absolute enough to zero a signal out."""
    pts = 0.0
    reasons: list[str] = []
    haystack = signal_text.lower()

    for rule in rules:
        if not getattr(rule, "enabled", True):
            continue
        pattern = (rule.pattern or "").lower().strip()
        if not pattern:
            continue
        hit = pattern in haystack or pattern == connector_key
        if not hit:
            continue
        if rule.kind == "mute":
            pts -= 40 * rule.weight
            reasons.append(f"you muted '{rule.pattern}'")
        elif rule.kind == "boost":
            pts += 18 * rule.weight
            reasons.append(f"you boosted '{rule.pattern}'")
        elif rule.kind == "vip":
            pts += 20 * rule.weight
            reasons.append(f"'{rule.pattern}' is on your VIP list")
    return Factor("preference", pts, "; ".join(reasons))


def historical_factor(kind: str, history: dict[str, dict[str, int]]) -> Factor:
    """Learn from what you actually did with this kind of thing before.

    `history[kind]` counts past outcomes. Dismissing five payment alerts in a row
    should quiet the sixth; acting on them should sharpen it. This is the slot
    where a trained model would go; the counting version is deliberately simple
    so its behaviour stays predictable.
    """
    stats = history.get(kind)
    if not stats:
        return Factor("history", 0.0, "")
    handled = stats.get("handled", 0)
    dismissed = stats.get("dismissed", 0)
    total = handled + dismissed
    if total < 3:
        return Factor("history", 0.0, "")

    ratio = handled / total
    if ratio >= 0.7:
        return Factor("history", 10.0, f"you usually act on {kind.replace('_', ' ')}")
    if ratio <= 0.25:
        return Factor("history", -14.0, f"you usually dismiss {kind.replace('_', ' ')}")
    return Factor("history", 0.0, "")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def score_signal(
    *,
    kind: str,
    connector_key: str,
    title: str,
    body: str = "",
    actor: str = "",
    occurred_at: datetime | None = None,
    hints: dict | None = None,
    rules: list | None = None,
    vips: set[str] | None = None,
    history: dict[str, dict[str, int]] | None = None,
    local_hour: int | None = None,
    quiet_hours: dict | None = None,
) -> Verdict:
    hints = hints or {}
    occurred_at = occurred_at or datetime.now(timezone.utc)
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)

    factors = [
        kind_factor(kind, connector_key),
        urgency_factor(occurred_at, hints, kind),
        financial_factor(hints),
        relationship_factor(actor, hints, vips or set()),
        importance_factor(hints),
        noise_factor(hints),
        preference_factor(f"{title} {body} {actor}", connector_key, rules or []),
        historical_factor(kind, history or {}),
    ]

    raw = sum(f.points for f in factors)
    score = max(0.0, min(100.0, raw))

    reasons = [f.reason for f in factors if f.reason]

    disposition = _disposition_for(score, local_hour, quiet_hours, reasons)
    return Verdict(score=score, disposition=disposition, factors=factors, reasons=reasons)


def _disposition_for(
    score: float,
    local_hour: int | None,
    quiet_hours: dict | None,
    reasons: list[str],
) -> Disposition:
    if score < DEFER_THRESHOLD:
        return Disposition.IGNORE
    if score < BRIEFING_THRESHOLD:
        return Disposition.DEFER
    if score < NOTIFY_THRESHOLD:
        return Disposition.BRIEFING

    # Clears the notify bar - but not during quiet hours unless it is an emergency.
    if local_hour is not None and quiet_hours and _in_quiet_hours(local_hour, quiet_hours):
        if score < EMERGENCY_THRESHOLD:
            reasons.append("held for morning - you are in quiet hours")
            return Disposition.BRIEFING
        reasons.append("important enough to break quiet hours")
    return Disposition.NOTIFY_NOW


def _in_quiet_hours(hour: int, quiet: dict) -> bool:
    start = int(quiet.get("start", 21))
    end = int(quiet.get("end", 7))
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # window wraps midnight


def explain(verdict: Verdict) -> str:
    """One-line 'why' for the UI."""
    if not verdict.reasons:
        return f"Scored {verdict.score:.0f}/100."
    top = "; ".join(verdict.reasons[:3])
    return f"{top} (scored {verdict.score:.0f}/100)."
