"""Health, observability, dashboards, syncing and destructive maintenance."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, or_, select

from ..agents.base import AgentContext
from ..agents.registry import describe_all
from ..agents.workers import AnalyticsAgent
from ..connectors import registry
from ..core import audit, monitor, telemetry
from ..core.scheduler import scheduler
from ..llm.client import get_llm
from ..models import (
    Approval,
    ApprovalStatus,
    AuditLog,
    Connection,
    Disposition,
    Memory,
    Message,
    Signal,
    Task,
    TaskStatus,
    TaskStep,
)
from .deps import ActorDep, DbDep, UserDep

router = APIRouter(tags=["system"])

VERSION = "0.1.0"


@router.get("/health")
def health(db: DbDep) -> dict[str, Any]:
    llm_ok, llm_detail = get_llm().available()
    try:
        db.execute(select(func.count(Signal.id)))
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "version": VERSION,
        "db": db_ok,
        "llm": {"ok": llm_ok, "detail": llm_detail, "provider": get_llm().provider},
        "scheduler": scheduler.status(),
    }


@router.get("/me")
def me(db: DbDep, user: UserDep) -> dict[str, Any]:
    def count(model, *where) -> int:
        return db.execute(
            select(func.count(model.id)).where(model.user_id == user.id, *where)
        ).scalar_one()

    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "timezone": user.timezone,
        "role": user.role,
        "quiet_hours": user.quiet_hours or {"start": 21, "end": 7},
        "briefing_optin": user.briefing_optin,
        "counts": {
            "pending_approvals": count(Approval, Approval.status == ApprovalStatus.PENDING.value),
            "unread_signals": count(Signal, Signal.state == "new"),
            "urgent_signals": count(
                Signal,
                Signal.disposition == Disposition.NOTIFY_NOW.value,
                Signal.state.in_(["new", "surfaced"]),
            ),
            "running_tasks": count(
                Task, Task.status.in_([TaskStatus.QUEUED.value, TaskStatus.RUNNING.value])
            ),
            "connections": count(Connection, Connection.status == "connected"),
            "memories": count(Memory),
        },
    }


# --------------------------------------------------------------------------- #
@router.get("/agents")
def agents(db: DbDep, user: UserDep) -> dict[str, Any]:
    """Agent health: definition plus observed runtime behaviour."""
    mine = select(TaskStep.task_id).join(Task, Task.id == TaskStep.task_id).where(
        Task.user_id == user.id
    )
    stats = dict(
        db.execute(
            select(TaskStep.agent, func.count(TaskStep.id))
            .where(TaskStep.task_id.in_(mine))
            .group_by(TaskStep.agent)
        ).all()
    )
    durations = dict(
        db.execute(
            select(TaskStep.agent, func.avg(TaskStep.duration_ms))
            .where(TaskStep.task_id.in_(mine)).group_by(TaskStep.agent)
        ).all()
    )
    errors = dict(
        db.execute(
            select(TaskStep.agent, func.count(TaskStep.id))
            .where(TaskStep.status == "error", TaskStep.task_id.in_(mine))
            .group_by(TaskStep.agent)
        ).all()
    )
    last_runs = dict(
        db.execute(
            select(TaskStep.agent, func.max(TaskStep.finished_at))
            .where(TaskStep.task_id.in_(mine)).group_by(TaskStep.agent)
        ).all()
    )

    out = []
    for spec in describe_all():
        name = spec["name"]
        runs = stats.get(name, 0)
        failed = errors.get(name, 0)
        last = last_runs.get(name)
        out.append(
            {
                **spec,
                "runs": runs,
                "errors": failed,
                "avg_ms": int(durations.get(name) or 0),
                "last_run": last.isoformat() if isinstance(last, datetime) else None,
                "status": "error" if failed and failed == runs else ("ok" if runs else "idle"),
            }
        )
    from ..core.sandbox import sandbox

    ok, detail = get_llm().available()
    return {
        "agents": out,
        "llm": {"ok": ok, "detail": detail, "provider": get_llm().provider},
        "sandbox": sandbox.available(),
    }


@router.get("/dashboards/executive")
def executive_dashboard(db: DbDep, user: UserDep) -> dict[str, Any]:
    """Computed live from the signal store, not cached."""
    agent = AnalyticsAgent()
    ctx = AgentContext(db=db, user=user, llm=get_llm(), intent="executive dashboard")
    result = agent.run(ctx)
    dashboard = result.artifacts[0] if result.artifacts else {"tiles": [], "series": []}
    return {
        "title": dashboard.get("title", "Operating snapshot"),
        "generated_at": dashboard.get("generated_at"),
        "tiles": dashboard.get("tiles", []),
        "series": dashboard.get("series", []),
        "finance": result.output.get("finance", {}),
        "forecast": result.output.get("forecast", {}),
    }


@router.get("/metrics")
def metrics(db: DbDep, user: UserDep) -> dict[str, Any]:
    """Agent health, autonomy health and what the background work is costing."""
    return telemetry.collect(db, user_id=user.id)


@router.get("/metrics/prometheus", response_class=PlainTextResponse)
def metrics_prometheus(db: DbDep, user: UserDep) -> str:
    return telemetry.prometheus(telemetry.collect(db, user_id=user.id))


@router.get("/audit")
def audit_log(db: DbDep, user: UserDep, limit: int = 100) -> dict[str, Any]:
    # Scoped to the acting user. System-level rows (user_id NULL) are shown
    # too, since they describe this installation rather than another tenant.
    rows = list(
        db.execute(
            select(AuditLog)
            .where(or_(AuditLog.user_id == user.id, AuditLog.user_id.is_(None)))
            .order_by(AuditLog.id.desc())
            .limit(min(limit, 500))
        ).scalars()
    )
    return {
        "entries": [
            {
                "id": r.id,
                "at": r.at.isoformat(),
                "actor": r.actor,
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "outcome": r.outcome,
                "detail": r.detail or {},
            }
            for r in rows
        ]
    }


@router.get("/audit/verify")
def audit_verify(db: DbDep, user: UserDep) -> dict[str, Any]:
    return audit.verify_chain(db)


@router.get("/memory")
def memories(db: DbDep, user: UserDep, kind: str | None = None, limit: int = 50) -> dict[str, Any]:
    stmt = select(Memory).where(Memory.user_id == user.id)
    if kind:
        stmt = stmt.where(Memory.kind == kind)
    rows = list(
        db.execute(stmt.order_by(Memory.salience.desc(), Memory.created_at.desc()).limit(limit)).scalars()
    )
    return {
        "memories": [
            {
                "id": m.id,
                "kind": m.kind,
                "content": m.content,
                "tags": m.tags or [],
                "source": m.source,
                "salience": m.salience,
                "use_count": m.use_count,
                "created_at": m.created_at.isoformat(),
            }
            for m in rows
        ]
    }


# --------------------------------------------------------------------------- #
# Syncing real apps
# --------------------------------------------------------------------------- #
@router.post("/sync")
def sync_all(db: DbDep, user: ActorDep) -> dict[str, Any]:
    """Check every connected app now, instead of waiting for the next sweep."""
    return monitor.sweep_user(db, user, force=True)


@router.post("/connectors/{key}/sync")
def sync_one(key: str, db: DbDep, user: ActorDep) -> dict[str, Any]:
    """Check a single app now."""
    conn = db.execute(
        select(Connection).where(
            Connection.user_id == user.id, Connection.connector_key == key
        )
    ).scalar_one_or_none()
    if conn is None:
        raise HTTPException(status_code=404, detail=f"{key} is not connected")
    if not registry.available(key):
        raise HTTPException(
            status_code=409, detail=f"{key} has no integration yet, so there is nothing to sync."
        )

    result = monitor.sweep_connection(db, user, conn)
    return {"ok": not result["errors"], "connector_key": key, **result}


# --------------------------------------------------------------------------- #
# Destructive maintenance
# --------------------------------------------------------------------------- #
@router.post("/admin/reset")
def admin_reset(db: DbDep, user: ActorDep) -> dict[str, Any]:
    """Erase everything this account holds, including stored credentials.

    Deliberately not reversible and deliberately not in the header: this
    disconnects every app and destroys the vault entries behind them. The audit
    chain is left intact - it is the record that this happened.
    """
    from ..core.vault import get_vault

    deleted: dict[str, int] = {}
    for model in (Message, Signal, TaskStep, Approval, Task, Memory, Connection):
        if model is TaskStep:
            rows = db.execute(
                select(TaskStep).where(
                    TaskStep.task_id.in_(select(Task.id).where(Task.user_id == user.id))
                )
            ).scalars().all()
        else:
            rows = db.execute(select(model).where(model.user_id == user.id)).scalars().all()
        deleted[model.__name__] = len(rows)
        for row in rows:
            db.delete(row)

    destroyed = get_vault().forget_user(user.id)
    deleted["credentials"] = destroyed

    audit.record(
        db,
        action="admin.reset",
        actor=f"user:{user.email}",
        user_id=user.id,
        resource_type="user",
        resource_id=user.id,
        detail={"deleted": deleted},
    )
    db.commit()
    registry.reset_cache()
    return {"ok": True, "deleted": deleted}
