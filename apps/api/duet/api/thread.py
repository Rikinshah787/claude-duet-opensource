"""The thread and the morning brief.

One continuous conversation per user. A quick question and a week-long project
both start here, which is why a message can carry a task, an approval or a
briefing rather than only text.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..core import briefing as briefing_mod
from ..core.orchestrator import create_task
from ..models import Briefing, Message, Task
from .deps import ActorDep, DbDep, UserDep

router = APIRouter(tags=["thread"])


class SendMessage(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


# A greeting or a bare acknowledgement. Answered inline: spinning up an agent
# task for "hi" would be both slow and silly.
CONVERSATIONAL = re.compile(
    r"^\s*(hi|hey|hello|thanks|thank you|ok|okay|got it|cool|nice|yes|no|sure|"
    r"how are you|how(?:'?s| is) it going)\b[\s!.?]*$",
    re.I,
)

# A question about current state, answered from what is already stored.
#
# Anchored to the whole message on purpose. The previous pattern used `.search`,
# so "what do I need to send to Priya" matched on "what do i need" and came back
# as a status summary instead of being done. Matching end to end keeps a
# question a question and a request a request.
#
# "what needs me" leads the list because the greeting tells people to say
# exactly that, and it used to fall through to a full agent task - the worst
# possible response to a phrase the product itself suggests.
STATUS_QUERY = re.compile(
    r"^\s*(?:(?:hi|hey|hello)[,\s]+)?(?:"
    r"what(?:'?s)?\s+needs?\s+(?:me|my\s+attention)|"
    r"what\s+do\s+i\s+need(?:\s+to\s+know)?|"
    r"(?:what'?s|whats|what\s+is|how'?s|hows|how\s+is)\s+"
    r"(?:up|new|goin[g]?(?:\s+on)?|happening|the\s+status|my\s+status)|"
    r"anything\s+(?:new|urgent|important|for\s+me|i\s+should\s+know)|"
    r"catch\s+me\s+up|"
    r"what\s+should\s+i\s+(?:look\s+at|know)|"
    r"status"
    r")\b[\s!.?]*$",
    re.I,
)


# A question about whether an app is linked: "is email connected?", "what apps
# are connected?". Answered from the connections table, never by a model.
#
# This used to become a full agent task whose answer an LLM composed from
# whatever stored emails happened to match the word "email". That is inference
# dressed as fact: it replied "yes, Gmail is connected" because messages existed,
# which stays true for days after a grant is revoked. Asked about an app with no
# messages it did worse - it listed the connectors of the machine running the
# model and sent the user to another product's settings page to fix them. The
# connections table knows the answer exactly, in one query, so it answers.
# About one app. Only acted on when the sentence actually names an app, which is
# what keeps "are the invoices linked" a piece of work rather than a status line.
#
# The trailing group exists for "is app connected gmail", where the app comes
# after the verb. It refuses a trailing clause opening with to/with/into/and,
# because those join two things rather than name one: "is the figma file linked
# to the notion page" names two apps and is still a question about the user's
# documents, not about Duet.
CONNECTION_QUERY = re.compile(
    r"^\s*(?:(?:hi|hey|hello)[,\s]+)?(?:"
    r"(?:is|are|was|were)\s+(?:my\s+|the\s+)?[\w\s.&'-]{0,40}?"
    r"(?:connected|linked|hooked\s*up)"
    r"(?:\s+(?!to\b|with\b|into\b|and\b|for\b)[\w.&'-]+){0,3}|"
    r"am\s+i\s+connected(?:\s+to\s+[\w\s.&'-]{0,40})?|"
    r"(?:did|does|do)\s+[\w\s.&'-]{0,40}?\s*connect(?:ed)?"
    r")\b[\s!.?]*$",
    re.I,
)

# About the apps as a group. These answer with the whole list and need no app to
# be named, so they are matched first and gate nothing.
CONNECTIONS_OVERVIEW = re.compile(
    r"^\s*(?:(?:hi|hey|hello)[,\s]+)?(?:"
    r"(?:what|which)(?:'?s)?\s*"
    r"(?:apps?|connectors?|accounts?|integrations?)?\s*"
    r"(?:is|are)?\s*connected|"
    r"(?:is|are)\s+(?:my\s+|the\s+)?"
    r"(?:apps?|connectors?|accounts?|integrations?|everything|anything|all)"
    r"[\w\s.&'-]{0,20}?(?:connected|linked|hooked\s*up)|"
    r"(?:show|list|check)\s+(?:me\s+)?(?:my\s+)?(?:connected\s+)?"
    r"(?:apps?|connectors?|integrations?|connections?)|"
    r"connections?\s+status|"
    r"(?:apps?|connectors?|connections?)\s+status"
    r")\b[\s!.?]*$",
    re.I,
)

# Words people use for an app that are not its name. Names and keys come from
# the catalog, so a new connector becomes answerable the day it is added.
CONNECTOR_WORDS: dict[str, tuple[str, ...]] = {
    "email": ("gmail", "outlook", "imap"),
    "mail": ("gmail", "outlook", "imap"),
    "inbox": ("gmail", "outlook", "imap"),
    "calendar": ("google_calendar", "outlook_calendar", "calendly"),
    "chat": ("slack", "teams", "discord"),
    "drive": ("google_drive",),
    "sheets": ("google_sheets",),
    "spreadsheet": ("google_sheets",),
    "spreadsheets": ("google_sheets",),
}


def _serialize_message(m: Message) -> dict[str, Any]:
    return {
        "id": m.id,
        "role": m.role,
        "kind": m.kind,
        "content": m.content,
        "task_id": m.task_id,
        "approval_id": m.approval_id,
        "signal_id": m.signal_id,
        "proactive": m.proactive,
        "meta": m.meta or {},
        "created_at": m.created_at.isoformat(),
    }


@router.get("/thread")
def get_thread(db: DbDep, user: UserDep, limit: int = 120) -> dict[str, Any]:
    rows = list(
        db.execute(
            select(Message)
            .where(Message.user_id == user.id)
            .order_by(Message.created_at.desc())
            .limit(min(limit, 500))
        ).scalars()
    )
    rows.reverse()
    return {"messages": [_serialize_message(m) for m in rows]}


@router.post("/thread")
def post_thread(payload: SendMessage, db: DbDep, user: ActorDep) -> dict[str, Any]:
    text = payload.text.strip()

    message = Message(user_id=user.id, role="user", kind="text", content=text)
    db.add(message)
    db.commit()

    # Connection questions are checked before everything else because they are
    # the most specific: "what's connected?" is also a status question, but the
    # answer the user wants is the list of apps, not the list of urgent things.
    if CONNECTIONS_OVERVIEW.match(text):
        reply = _connection_reply(db, user, [])
        return {"message": _serialize_message(message),
                "reply": _serialize_message(reply), "task": None}

    if CONNECTION_QUERY.match(text):
        specific, generic = _mentioned_connectors(text)
        targets = _connection_targets(db, user, specific, generic)
        # Only when an app was actually named. Anything else that happens to
        # match the shape of the question is left to a real task.
        if targets:
            reply = _connection_reply(db, user, targets)
            return {"message": _serialize_message(message),
                    "reply": _serialize_message(reply), "task": None}

    # Status is checked next: "what's up" parses as a greeting but is a real
    # question, and answering it with "Morning." would be useless.
    if STATUS_QUERY.match(text):
        reply = _status_reply(db, user)
        return {"message": _serialize_message(message), "reply": _serialize_message(reply),
                "task": None}

    if CONVERSATIONAL.match(text):
        reply = _acknowledge(db, user, text)
        return {"message": _serialize_message(message), "reply": _serialize_message(reply),
                "task": None}

    task = create_task(db, user, text, origin="user")
    ack = Message(
        user_id=user.id,
        role="assistant",
        kind="task_update",
        content=f"On it - {task.title}",
        task_id=task.id,
        meta={"status": "queued"},
    )
    db.add(ack)
    db.commit()

    return {
        "message": _serialize_message(message),
        "reply": _serialize_message(ack),
        "task": {"id": task.id, "title": task.title, "status": task.status},
    }


def _local_now(user: UserDep) -> datetime:
    """The user's wall-clock time.

    `User.timezone` defaults to "UTC", which in practice means "nobody set
    this". Honouring that literally is how the assistant came to say "Morning"
    at five in the afternoon. Since this runs on the user's own machine, the
    system clock is a far better guess than a timezone nobody chose - so the
    stored zone wins only when it was actually set to something.
    """
    name = (getattr(user, "timezone", "") or "").strip()
    if name and name.upper() != "UTC":
        try:
            return datetime.now(ZoneInfo(name))
        except Exception:
            # An unknown or misspelled zone is not worth failing a greeting over.
            pass
    return datetime.now().astimezone()


def _part_of_day(now: datetime) -> str:
    hour = now.hour
    if 5 <= hour < 12:
        return "Morning"
    if 12 <= hour < 17:
        return "Afternoon"
    if 17 <= hour < 22:
        return "Evening"
    return "Late one"


# Said instead of the full greeting when someone says hello twice in a row.
# Replaying the identical sentence is what made "hi" feel unanswered: the user
# said it four times and got the same forty-three words back each time, which
# reads as a recording rather than a reply.
GREETING_AGAIN: tuple[str, ...] = (
    "Still quiet.",
    "Nothing new since then.",
    "Same as a moment ago - nothing needs you.",
)

# How long a greeting counts as already given.
GREETING_WINDOW = timedelta(minutes=15)


def _greetings_just_now(db: DbDep, user: UserDep) -> int:
    """How many greetings were already given in the last few minutes.

    Read from the messages themselves rather than kept in memory, so it
    survives a restart and stays right with two windows open.
    """
    rows = db.execute(
        select(Message)
        .where(
            Message.user_id == user.id,
            Message.role == "assistant",
            Message.created_at >= datetime.now(timezone.utc) - GREETING_WINDOW,
        )
        .order_by(Message.created_at.desc())
        .limit(8)
    ).scalars().all()
    return sum(1 for m in rows if (m.meta or {}).get("greeting"))


def _acknowledge(db: DbDep, user: UserDep, text: str) -> Message:
    lowered = text.lower()
    meta: dict[str, Any] = {}

    if lowered.startswith(("thank", "thanks")):
        content = "Any time."
    elif lowered.startswith(("hi", "hey", "hello")):
        said = _greetings_just_now(db, user)
        if said:
            content = GREETING_AGAIN[(said - 1) % len(GREETING_AGAIN)]
        else:
            content = f"{_part_of_day(_local_now(user))}. {_standing(db, user)}"
        meta = {"greeting": True, "repeat": said}
    else:
        content = "Noted."

    reply = Message(
        user_id=user.id, role="assistant", kind="text", content=content, meta=meta
    )
    db.add(reply)
    db.commit()
    return reply


def _standing(db: DbDep, user: UserDep) -> str:
    """One sentence of real state, for the back half of a greeting.

    A greeting that says nothing is a greeting that wastes a turn. This costs
    three indexed counts and no model call, so "hi" stays instant while still
    telling the user something true: what is waiting, or - just as useful -
    that something was read and none of it needed them.
    """
    from ..models import Approval, ApprovalStatus, Disposition, Signal

    urgent = db.execute(
        select(func.count())
        .select_from(Signal)
        .where(
            Signal.user_id == user.id,
            Signal.disposition == Disposition.NOTIFY_NOW.value,
            Signal.state.in_(["new", "surfaced"]),
        )
    ).scalar_one()
    if urgent:
        thing = "thing wants" if urgent == 1 else "things want"
        return f"{urgent} {thing} you now - say \u201cwhat needs me?\u201d and I will lay them out."

    pending = db.execute(
        select(func.count())
        .select_from(Approval)
        .where(
            Approval.user_id == user.id,
            Approval.status == ApprovalStatus.PENDING.value,
        )
    ).scalar_one()
    if pending:
        noun = "action is" if pending == 1 else "actions are"
        return f"{pending} {noun} drafted and waiting on your approval."

    # "I read 40 things and none need you" is true but says nothing about the
    # judgement behind it. The split does: it is the difference between an
    # assistant that is working and one that is merely quiet.
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    seen = db.execute(
        select(func.count())
        .select_from(Signal)
        .where(Signal.user_id == user.id, Signal.created_at >= since)
    ).scalar_one()
    if not seen:
        return "Nothing has come in yet. Ask me for anything."

    worth_a_look = db.execute(
        select(func.count())
        .select_from(Signal)
        .where(
            Signal.user_id == user.id,
            Signal.created_at >= since,
            Signal.disposition.in_(
                [Disposition.BRIEFING.value, Disposition.DEFER.value]
            ),
        )
    ).scalar_one()
    if worth_a_look:
        return (
            f"I have read {seen} things in the last day. {worth_a_look} are worth a "
            f"look when you have time; none need you now."
        )
    return f"I have read {seen} things in the last day and none of them need you."


def _status_reply(db: DbDep, user: UserDep) -> Message:
    """Answer from stored state rather than re-running a sweep."""
    from ..models import Approval, ApprovalStatus, Disposition, Signal, TaskStatus

    pending = db.execute(
        select(Approval).where(
            Approval.user_id == user.id, Approval.status == ApprovalStatus.PENDING.value
        )
    ).scalars().all()

    urgent = db.execute(
        select(Signal)
        .where(
            Signal.user_id == user.id,
            Signal.disposition == Disposition.NOTIFY_NOW.value,
            Signal.state.in_(["new", "surfaced"]),
        )
        .order_by(Signal.score.desc())
        .limit(4)
    ).scalars().all()

    running = db.execute(
        select(Task).where(
            Task.user_id == user.id,
            Task.status.in_([TaskStatus.QUEUED.value, TaskStatus.RUNNING.value]),
        )
    ).scalars().all()

    lines: list[str] = []
    if urgent:
        lines.append(f"{len(urgent)} thing(s) want you now:")
        lines += [f"- {s.title}" for s in urgent]
    if pending:
        lines.append("")
        lines.append(f"{len(pending)} action(s) drafted and waiting on your approval.")
    if running:
        lines.append(f"{len(running)} task(s) still running.")
    if not lines:
        lines.append("Nothing urgent. Your connected apps are quiet.")

    reply = Message(
        user_id=user.id,
        role="assistant",
        kind="text",
        content="\n".join(lines),
        meta={"urgent": len(urgent), "pending": len(pending)},
    )
    db.add(reply)
    db.commit()
    return reply


# --------------------------------------------------------------------------- #
# "is it connected?"
# --------------------------------------------------------------------------- #
def _mentioned_connectors(text: str) -> tuple[list[str], list[str]]:
    """Apps a sentence names, split into the explicit and the implied.

    "is gmail connected" names one app. "is email connected" implies three, and
    the difference matters: an app the user named is worth answering about even
    when it has never been connected, while an implied one is only worth
    mentioning if there is something to say about it.
    """
    from ..connectors.catalog import BY_KEY, CATALOG

    lowered = text.lower()
    specific: list[str] = []
    generic: list[str] = []

    # Longest phrase first, so "google calendar" is matched as itself rather
    # than reduced to the "calendar" alias.
    phrases: list[tuple[str, tuple[str, ...], bool]] = []
    for spec in CATALOG:
        phrases.append((spec.name.lower(), (spec.key,), True))
        phrases.append((spec.key.replace("_", " "), (spec.key,), True))
    phrases += [(word, keys, False) for word, keys in CONNECTOR_WORDS.items()]
    phrases.sort(key=lambda row: len(row[0]), reverse=True)

    for phrase, keys, is_specific in phrases:
        if not re.search(rf"\b{re.escape(phrase)}\b", lowered):
            continue
        bucket = specific if is_specific else generic
        for key in keys:
            if key in BY_KEY and key not in specific and key not in generic:
                bucket.append(key)

    return specific, generic


def _connection_targets(
    db: DbDep, user: UserDep, specific: list[str], generic: list[str]
) -> list[str]:
    """Which apps to actually report on.

    An app the user named is always reported. An implied one is narrowed down,
    in this order: the ones that are actually connected, then the ones that
    exist at all, then the first as a stand-in so the answer is "no" rather than
    silence. "Is email connected?" should answer "yes, Gmail" without also
    reciting a failed IMAP attempt from last week - the question was about
    whether mail is working, and it is.
    """
    from ..models import Connection

    if specific:
        return specific
    if not generic:
        return []

    rows = {
        c.connector_key: c
        for c in db.execute(
            select(Connection).where(
                Connection.user_id == user.id,
                Connection.connector_key.in_(generic),
            )
        ).scalars()
    }
    live = [k for k in generic if (c := rows.get(k)) and c.credential_ref]
    return live or [k for k in generic if k in rows] or generic[:1]


def _ago(when: datetime | None) -> str:
    if when is None:
        return "never"
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - when).total_seconds()
    if seconds < 90:
        return "just now"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)} minutes ago"
    hours = minutes / 60
    if hours < 36:
        return f"{int(hours)} hour{'s' if int(hours) != 1 else ''} ago"
    return f"{int(hours / 24)} days ago"


def _brief(error: str, limit: int = 150) -> str:
    """The diagnosis without the stack of platform detail behind it."""
    text = " ".join(error.split())
    # Connector errors read "ConnectorError: could not reach X: [WinError ...]".
    # Everything from the bracket on describes the OS, not the problem.
    head = text.split("[", 1)[0].rstrip(" :")
    text = head if len(head) >= 24 else text
    return text if len(text) <= limit else text[: limit - 1].rstrip(" ,:;") + "\u2026"


def _one_connection(key: str, conn: Any, count: int) -> str:
    """One app, one sentence, from stored state only."""
    from ..connectors.catalog import BY_KEY

    spec = BY_KEY.get(key)
    name = spec.name if spec else key

    # Presence of a row is not connectedness - the credential is. A revoked row
    # is kept for its audit trail, and disconnecting clears the reference.
    if conn is None or not conn.credential_ref:
        if conn is not None and conn.last_error:
            return (
                f"No - {name} is not connected. The last attempt failed: "
                f"{_brief(conn.last_error)}"
            )
        if spec is not None and not spec.has_adapter:
            return (
                f"No - {name} is in the catalog but has no working integration yet, "
                f"so there is nothing to connect."
            )
        return f"No - {name} is not connected. Open Apps, pick {name}, and sign in."

    where = f" ({conn.account_label})" if conn.account_label else ""

    if conn.status == "error":
        detail = (
            f" The last check failed: {_brief(conn.last_error)}"
            if conn.last_error
            else ""
        )
        return f"{name} is connected{where} but not working.{detail}"
    if not conn.enabled:
        return f"{name} is connected{where} but switched off, so nothing is being read."

    read = f" {count} items read." if count else " Nothing read from it yet."
    return f"Yes - {name} is connected{where}. Last checked {_ago(conn.last_sync_at)}.{read}"


def _connection_reply(db: DbDep, user: UserDep, keys: list[str]) -> Message:
    from ..connectors.catalog import BY_KEY
    from ..models import Connection, Signal

    rows = {
        c.connector_key: c
        for c in db.execute(
            select(Connection).where(Connection.user_id == user.id)
        ).scalars()
    }
    counts = dict(
        db.execute(
            select(Signal.connector_key, func.count(Signal.id))
            .where(Signal.user_id == user.id)
            .group_by(Signal.connector_key)
        ).all()
    )

    lines: list[str] = []

    if keys:
        lines = [_one_connection(k, rows.get(k), counts.get(k, 0)) for k in keys]
    else:
        live = sorted(
            (k, c) for k, c in rows.items()
            if c.credential_ref and c.status == "connected"
        )
        broken = sorted(
            (k, c) for k, c in rows.items()
            if c.credential_ref and c.status != "connected"
        )
        gone = sorted(k for k, c in rows.items() if not c.credential_ref)

        if not live and not broken:
            lines.append("Nothing is connected yet. Open Apps to link your first one.")
        else:
            if live:
                noun = "app" if len(live) == 1 else "apps"
                lines.append(f"{len(live)} {noun} connected:")
                for key, conn in live:
                    spec = BY_KEY.get(key)
                    label = f" ({conn.account_label})" if conn.account_label else ""
                    lines.append(
                        f"- {spec.name if spec else key}{label} - "
                        f"{counts.get(key, 0)} items read, "
                        f"last checked {_ago(conn.last_sync_at)}"
                    )
            for key, conn in broken:
                spec = BY_KEY.get(key)
                lines.append(f"- {spec.name if spec else key} is connected but failing.")

        if gone:
            names = ", ".join((BY_KEY[k].name if k in BY_KEY else k) for k in gone)
            lines.append(f"Previously connected, now disconnected: {names}.")

    reply = Message(
        user_id=user.id,
        role="assistant",
        kind="text",
        content="\n".join(lines),
        meta={"answered": "connections", "connectors": keys},
    )
    db.add(reply)
    db.commit()
    return reply


# --------------------------------------------------------------------------- #
def _serialize_briefing(b: Briefing) -> dict[str, Any]:
    return {
        "id": b.id,
        "for_date": b.for_date,
        "headline": b.headline,
        "sections": b.sections or [],
        "model": b.model,
        "created_at": b.created_at.isoformat(),
        "delivered_at": b.delivered_at.isoformat() if b.delivered_at else None,
    }


@router.get("/briefing/latest")
def latest_briefing(db: DbDep, user: UserDep) -> dict[str, Any]:
    b = briefing_mod.latest(db, user.id)
    return {"briefing": _serialize_briefing(b) if b else None}


@router.post("/briefing/generate")
def generate_briefing(db: DbDep, user: ActorDep) -> dict[str, Any]:
    b = briefing_mod.generate(db, user)
    return {"briefing": _serialize_briefing(b)}


@router.get("/briefing/{briefing_id}")
def get_briefing(briefing_id: str, db: DbDep, user: UserDep) -> dict[str, Any]:
    b = db.get(Briefing, briefing_id)
    if b is None or b.user_id != user.id:
        raise HTTPException(status_code=404, detail="No such briefing")
    return {"briefing": _serialize_briefing(b)}
