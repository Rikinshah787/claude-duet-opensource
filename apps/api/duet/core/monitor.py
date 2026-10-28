"""The proactive sweep.

Polls every connected, monitored app; scores what comes back; and decides
whether anything warrants interrupting the user.

Three design points worth stating, because they are what separate a useful
proactive agent from an annoying one:

* **The sweep is read-only.** It can never write to a connected app. It observes
  and scores; acting is a separate, gated path.
* **Interrupting is rationed.** Even when several things clear the notify bar,
  the user gets one grouped message, not five. A burst of notifications is how a
  proactive assistant teaches someone to ignore it.
* **Checking is not free.** Scoring itself is arithmetic and costs no model
  turns, but every sweep is one API call per connected app. With 18 apps at a
  900s interval that is ~1,700 vendor calls a day; at 60s it would be ~26,000.
  The interval is minutes-to-hours for rate limits and politeness, not because
  the maths is expensive.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..agents.memory import behaviour_history
from ..connectors import catalog, registry
from ..connectors.base import CredentialHandle, FetchContext
from ..models import (
    Connection,
    Disposition,
    Message,
    PolicyRule,
    Signal,
    User,
)
from . import audit, injection
from .priority import score_signal

log = logging.getLogger("duet.monitor")

# Never send more than this many proactive pings in one sweep, per user.
MAX_INTERRUPTS_PER_SWEEP = 1


def sweep_user(db: Session, user: User, *, force: bool = False) -> dict[str, Any]:
    """Run one full pass over a user's connected apps."""
    connections = list(
        db.execute(
            select(Connection).where(
                Connection.user_id == user.id,
                Connection.enabled.is_(True),
                Connection.monitored.is_(True),
                # "error" is included deliberately: a connector that failed once
                # must be retried, or a single transient fault retires the app
                # permanently. Only an explicitly revoked connection stays out.
                Connection.status.in_(["connected", "error"]),
            )
        ).scalars()
    )

    rules = list(
        db.execute(
            select(PolicyRule).where(
                PolicyRule.user_id == user.id, PolicyRule.enabled.is_(True)
            )
        ).scalars()
    )
    vips = {r.pattern.lower() for r in rules if r.kind == "vip"}
    history = behaviour_history(db, user.id)
    local_hour = _local_hour(user)

    created: list[Signal] = []
    errors: list[dict[str, str]] = []

    for conn in connections:
        try:
            new_signals = _sweep_connection(
                db, user, conn, rules=rules, vips=vips, history=history, local_hour=local_hour
            )
            created.extend(new_signals)
            conn.last_sync_at = datetime.now(timezone.utc)
            conn.last_error = None
            # A connector that failed once and now succeeds must come back, or
            # one transient error silently retires the app forever.
            if conn.status == "error":
                conn.status = "connected"
        except Exception as exc:
            log.exception("sweep failed for %s", conn.connector_key)
            conn.last_error = f"{type(exc).__name__}: {exc}"[:500]
            conn.status = "error"
            errors.append({"connector": conn.connector_key, "error": str(exc)[:200]})
        db.commit()

    notified = _maybe_interrupt(db, user, created, force=force)

    audit.record(
        db,
        action="monitor.sweep",
        actor="system",
        user_id=user.id,
        resource_type="user",
        resource_id=user.id,
        detail={
            "connections": len(connections),
            "new_signals": len(created),
            "notified": len(notified),
            "errors": errors,
        },
    )
    db.commit()

    return {
        "connections_swept": len(connections),
        "new_signals": len(created),
        "notified": len(notified),
        "errors": errors,
        "signals": [
            {
                "id": s.id,
                "title": s.title,
                "score": round(s.score, 1),
                "disposition": s.disposition,
            }
            for s in sorted(created, key=lambda s: s.score, reverse=True)[:10]
        ],
    }


def sweep_connection(db: Session, user: User, conn: Connection) -> dict[str, Any]:
    """Sweep exactly one connection. Used by the per-app "Sync now" action."""
    rules = list(
        db.execute(
            select(PolicyRule).where(
                PolicyRule.user_id == user.id, PolicyRule.enabled.is_(True)
            )
        ).scalars()
    )
    vips = {r.pattern.lower() for r in rules if r.kind == "vip"}
    history = behaviour_history(db, user.id)

    errors: list[dict[str, str]] = []
    created: list[Signal] = []
    try:
        created = _sweep_connection(
            db, user, conn, rules=rules, vips=vips, history=history,
            local_hour=_local_hour(user),
        )
        conn.last_sync_at = datetime.now(timezone.utc)
        conn.last_error = None
        if conn.status == "error":
            conn.status = "connected"
    except Exception as exc:
        log.exception("sync failed for %s", conn.connector_key)
        conn.last_error = f"{type(exc).__name__}: {exc}"[:500]
        conn.status = "error"
        errors.append({"connector": conn.connector_key, "error": str(exc)[:200]})

    notified = _maybe_interrupt(db, user, created)
    db.commit()
    return {
        "new_signals": len(created),
        "notified": len(notified),
        "errors": [e["error"] for e in errors],
    }


def _sweep_connection(
    db: Session,
    user: User,
    conn: Connection,
    *,
    rules: list,
    vips: set[str],
    history: dict,
    local_hour: int,
) -> list[Signal]:
    adapter = registry.resolve(conn.connector_key)
    credential = (
        CredentialHandle(ref=conn.credential_ref, connector_key=conn.connector_key)
        if conn.credential_ref
        else None
    )
    # SQLite hands back naive datetimes even for timezone-aware columns, and an
    # adapter comparing those against aware timestamps raises. Normalise once,
    # here, so no adapter has to think about it.
    since = conn.last_sync_at
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    ctx = FetchContext(
        user_id=user.id,
        connection_id=conn.id,
        since=since,
        cursor=conn.cursor or {},
        credential=credential,
    )

    raw_signals, cursor = adapter.fetch(ctx)
    conn.cursor = cursor

    created: list[Signal] = []
    for raw in raw_signals:
        # Screen before scoring. Content arriving from a connected app is
        # written by third parties and may be addressed at the assistant rather
        # than the user; see core/injection.py.
        threat = injection.screen_signal(raw.title, raw.body)
        if threat.suspicious:
            raw.hints = {**raw.hints, "injection": threat.as_dict()}
            log.warning(
                "possible prompt injection in %s/%s: %s",
                conn.connector_key,
                raw.external_id,
                ",".join(threat.matches),
            )
            audit.record(
                db,
                action="security.injection_detected",
                actor="system",
                user_id=user.id,
                resource_type="signal",
                resource_id=raw.external_id,
                outcome="hostile" if threat.hostile else "flagged",
                detail={
                    "connector": conn.connector_key,
                    "patterns": threat.matches,
                    "score": threat.score,
                },
            )

        verdict = score_signal(
            kind=raw.kind,
            connector_key=conn.connector_key,
            title=raw.title,
            body=raw.body,
            actor=raw.actor,
            occurred_at=raw.occurred_at,
            hints=raw.hints,
            rules=rules,
            vips=vips,
            history=history,
            local_hour=local_hour,
            quiet_hours=user.quiet_hours,
        )

        signal = Signal(
            user_id=user.id,
            connection_id=conn.id,
            connector_key=conn.connector_key,
            external_id=raw.external_id,
            kind=raw.kind,
            title=raw.title[:500],
            body=raw.body,
            actor=raw.actor,
            url=raw.url,
            occurred_at=raw.occurred_at or datetime.now(timezone.utc),
            raw={"hints": raw.hints, **raw.raw},
            score=verdict.score,
            factors={f.name: round(f.points, 1) for f in verdict.factors},
            reasons=verdict.reasons,
            disposition=verdict.disposition.value,
        )
        db.add(signal)
        try:
            db.flush()  # the unique constraint is the dedupe
            created.append(signal)
        except IntegrityError:
            db.rollback()
            continue

    return created


def _maybe_interrupt(
    db: Session, user: User, signals: list[Signal], *, force: bool = False
) -> list[Signal]:
    """Decide whether to message the user now, and say why."""
    urgent = [s for s in signals if s.disposition == Disposition.NOTIFY_NOW.value]
    if not urgent:
        return []

    urgent.sort(key=lambda s: s.score, reverse=True)
    if not force:
        urgent = urgent[:MAX_INTERRUPTS_PER_SWEEP]

    now = datetime.now(timezone.utc)
    lead = urgent[0]
    others = [s for s in signals if s.disposition == Disposition.NOTIFY_NOW.value][1:]

    body = _compose_interrupt(lead, others)

    db.add(
        Message(
            user_id=user.id,
            role="assistant",
            kind="text",
            content=body,
            signal_id=lead.id,
            proactive=True,
            meta={
                "score": round(lead.score, 1),
                "reasons": lead.reasons[:4],
                "connector": lead.connector_key,
                "also": [s.title for s in others[:3]],
            },
        )
    )

    for s in urgent:
        s.state = "surfaced"
        s.surfaced_at = now

    return urgent


def _compose_interrupt(lead: Signal, others: list[Signal]) -> str:
    why = "; ".join(lead.reasons[:2]) or "it scored above your attention threshold"
    lines = [lead.title, "", f"Flagging now because {why}."]

    if lead.body:
        snippet = lead.body.strip().split("\n")[0][:220]
        lines.insert(1, snippet)

    if others:
        lines.append("")
        lines.append(
            f"{len(others)} other thing(s) came in that can wait for your brief."
        )
    return "\n".join(lines)


def _local_hour(user: User) -> int:
    """User-local hour, for quiet-hours logic."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(user.timezone)).hour
    except Exception:
        return datetime.now(timezone.utc).hour


def sweep_all(db: Session) -> dict[str, Any]:
    users = list(db.execute(select(User)).scalars())
    results = {}
    for user in users:
        results[user.email] = sweep_user(db, user)
    return results


def due_for_sweep(conn: Connection, interval_s: int) -> bool:
    if conn.last_sync_at is None:
        return True
    age = datetime.now(timezone.utc) - conn.last_sync_at.replace(tzinfo=timezone.utc)
    return age > timedelta(seconds=interval_s)
