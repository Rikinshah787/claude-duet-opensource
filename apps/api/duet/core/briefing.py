"""The morning brief.

One short report, assembled from everything the sweep collected since the last
one. The ordering rule is that the brief leads with what the reader has to
*decide*, not with what is merely new - a list sorted by arrival time is a
mailbox, and they already have one of those.

Sections are generated from scored signals, so the brief inherits the priority
engine's judgement rather than re-deriving it. The model, when present, only
writes the connective prose; it never selects what goes in, because selection is
the part that has to be auditable.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..llm.client import get_llm
from ..models import Approval, ApprovalStatus, Briefing, Disposition, Message, Signal, Task, User
from . import audit

# Section definitions: (title, matching kinds, lead-in when non-empty)
SECTIONS: tuple[tuple[str, set[str], str], ...] = (
    (
        "Money",
        {"invoice_overdue", "payment_failed", "dispute", "cash_risk", "bill_due",
         "payroll_due", "revenue_risk"},
        "Cash and billing",
    ),
    (
        "Customers",
        {"escalation", "sla_breach", "customer_message", "deal_risk", "deal_stale"},
        "Accounts that need a human",
    ),
    (
        "Your day",
        {"meeting", "conflict", "prep_needed", "booking"},
        "Calendar",
    ),
    (
        "Waiting on you",
        {"review_request", "signature_pending", "share_request", "thread_waiting",
         "vip_email", "dm", "mention"},
        "People blocked on your reply",
    ),
    (
        "Work",
        {"blocker", "ticket", "deadline", "task_due", "ci_failure", "incident",
         "inventory_low", "automation_failed", "metric_anomaly"},
        "Projects and systems",
    ),
)

BRIEF_SYSTEM = """You write a 2-sentence opener for an operator's morning brief.
You are given the day's items, already prioritised. Name the single thing that
most needs a decision today and why. Be specific - use the actual names and
numbers. No greeting, no "here's your brief", no sign-off. Under 45 words."""


def generate(db: Session, user: User, *, for_date: str | None = None, lookback_h: int = 24) -> Briefing:
    day = for_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_h)

    signals = list(
        db.execute(
            select(Signal)
            .where(
                Signal.user_id == user.id,
                Signal.occurred_at >= since,
                Signal.disposition.in_(
                    [Disposition.NOTIFY_NOW.value, Disposition.BRIEFING.value]
                ),
                Signal.state != "dismissed",
            )
            .order_by(Signal.score.desc())
        ).scalars()
    )

    pending = list(
        db.execute(
            select(Approval).where(
                Approval.user_id == user.id,
                Approval.status == ApprovalStatus.PENDING.value,
            )
        ).scalars()
    )

    sections = _build_sections(signals)
    if pending:
        sections.insert(
            0,
            {
                "title": "Needs your approval",
                "lead": "Prepared and waiting",
                "items": [
                    {
                        "title": a.title,
                        "detail": a.rationale[:220],
                        "score": 100,
                        "connector": a.connector_key or "",
                        "kind": a.action_type,
                        "approval_id": a.id,
                        "risk": a.risk_tier,
                    }
                    for a in pending[:6]
                ],
            },
        )

    headline = _headline(signals, pending, sections)
    recommendations = _recommendations(signals, pending)
    if recommendations:
        sections.append(
            {
                "title": "What I'd do first",
                "lead": "Suggestions",
                "items": recommendations,
            }
        )

    briefing = Briefing(
        user_id=user.id,
        for_date=day,
        headline=headline,
        sections=sections,
        signal_ids=[s.id for s in signals[:40]],
        model=get_llm().provider,
        delivered_at=datetime.now(timezone.utc),
    )
    db.add(briefing)
    db.flush()

    db.add(
        Message(
            user_id=user.id,
            role="assistant",
            kind="brief",
            content=headline,
            proactive=True,
            meta={
                "briefing_id": briefing.id,
                "sections": [s["title"] for s in sections],
                "item_count": sum(len(s["items"]) for s in sections),
            },
        )
    )

    for s in signals:
        if s.state == "new":
            s.state = "surfaced"
            s.surfaced_at = datetime.now(timezone.utc)

    audit.record(
        db,
        action="briefing.generated",
        actor="system",
        user_id=user.id,
        resource_type="briefing",
        resource_id=briefing.id,
        detail={"items": len(signals), "pending_approvals": len(pending)},
    )
    db.commit()
    return briefing


# --------------------------------------------------------------------------- #
def _build_sections(signals: list[Signal]) -> list[dict[str, Any]]:
    buckets: dict[str, list[Signal]] = defaultdict(list)
    claimed: set[str] = set()

    for title, kinds, lead in SECTIONS:
        for sig in signals:
            if sig.id in claimed or sig.kind not in kinds:
                continue
            buckets[title].append(sig)
            claimed.add(sig.id)

    leftovers = [s for s in signals if s.id not in claimed]
    if leftovers:
        buckets["Also"].extend(leftovers)

    order = [title for title, _, _ in SECTIONS] + ["Also"]
    leads = {title: lead for title, _, lead in SECTIONS}

    out: list[dict[str, Any]] = []
    for title in order:
        items = buckets.get(title, [])
        if not items:
            continue
        items.sort(key=lambda s: s.score, reverse=True)
        out.append(
            {
                "title": title,
                "lead": leads.get(title, "Everything else"),
                "items": [_item(s) for s in items[:6]],
                "truncated": max(0, len(items) - 6),
            }
        )
    return out


def _item(s: Signal) -> dict[str, Any]:
    return {
        "id": s.id,
        "title": s.title,
        "detail": (s.body or "").strip().split("\n")[0][:220],
        "actor": s.actor,
        "connector": s.connector_key,
        "kind": s.kind,
        "score": round(s.score, 1),
        "why": "; ".join(s.reasons[:2]),
        "when": s.occurred_at.isoformat(),
        "url": s.url,
    }


def _headline(signals: list[Signal], pending: list[Approval], sections: list[dict]) -> str:
    if not signals and not pending:
        return "Nothing needs you this morning. Your connected apps are quiet."

    top = signals[0] if signals else None
    counts = ", ".join(
        f"{len(s['items'])} {s['title'].lower()}" for s in sections[:3] if s["items"]
    )

    fallback = (
        f"{top.title}. " if top else ""
    ) + f"Across your apps: {counts}."
    if pending:
        fallback += f" {len(pending)} action(s) are drafted and waiting on you."

    llm = get_llm()
    if llm.provider == "heuristic" or not signals:
        return fallback[:480]

    from .injection import wrap_untrusted

    digest = "\n".join(
        f"- [{s.connector_key}/{s.kind}] {s.title} (priority {s.score:.0f})"
        for s in signals[:12]
    )
    if pending:
        digest += "\n" + "\n".join(f"- [awaiting approval] {a.title}" for a in pending[:4])

    # Subject lines come from third parties. A crafted one would otherwise be
    # read as an instruction while composing the brief.
    out = llm.complete(
        f"Today's items:\n{wrap_untrusted(digest, source='your connected apps')}",
        system=BRIEF_SYSTEM,
        temperature=0.3,
        max_tokens=140,
        fallback=fallback,
    )
    return (out.text or fallback)[:480]


def _recommendations(signals: list[Signal], pending: list[Approval]) -> list[dict[str, Any]]:
    """Concrete next steps, derived from the signals themselves.

    Each one names the thing it came from, so a suggestion can be checked rather
    than taken on faith.
    """
    recs: list[dict[str, Any]] = []

    overdue = [s for s in signals if s.kind == "invoice_overdue"]
    if overdue:
        total = sum(float((s.raw or {}).get("hints", {}).get("amount") or 0) for s in overdue)
        recs.append(
            {
                "title": f"Chase {len(overdue)} overdue invoice(s) worth ${total:,.0f}",
                "detail": "I can draft the reminders now; you approve before anything sends.",
                "action": "chase overdue invoices",
                "kind": "suggestion",
                "score": 90,
            }
        )

    conflicts = [s for s in signals if s.kind in {"conflict", "prep_needed"}]
    if conflicts:
        recs.append(
            {
                "title": f"Fix {len(conflicts)} calendar problem(s)",
                "detail": conflicts[0].title,
                "action": "resolve my calendar conflicts",
                "kind": "suggestion",
                "score": 85,
            }
        )

    escalations = [s for s in signals if s.kind in {"escalation", "deal_risk", "sla_breach"}]
    if escalations:
        recs.append(
            {
                "title": f"Respond to {len(escalations)} customer escalation(s)",
                "detail": escalations[0].title,
                "action": f"handle the escalation: {escalations[0].title}",
                "kind": "suggestion",
                "score": 88,
            }
        )

    prep = [s for s in signals if s.kind == "meeting"]
    if prep:
        recs.append(
            {
                "title": f"Prep for {prep[0].title}",
                "detail": "I'll pull the thread history and the numbers they'll ask about.",
                "action": f"prepare me for {prep[0].title}",
                "kind": "suggestion",
                "score": 80,
            }
        )

    return recs[:4]


def latest(db: Session, user_id: str) -> Briefing | None:
    return db.execute(
        select(Briefing)
        .where(Briefing.user_id == user_id)
        .order_by(Briefing.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
