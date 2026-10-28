"""Approval executor.

The only place in the system that performs a write against a third-party app.

Everything upstream - agents, planner, monitor - produces intent. This module
turns an *approved* intent into a real call, and nothing reaches it without a
recorded human decision. Keeping that single chokepoint is what makes the
guarantee checkable: if you want to know whether the assistant can send
something without asking, you only have to audit this file.

Re-checks at execution time, because approval and execution are separated in
time and the world can move in between:
  * the approval is still PENDING (not already executed, rejected or expired)
  * it has not passed its expiry
  * the connector is still connected and enabled
  * forbidden actions are refused even if a row somehow says approved
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..agents.security import FORBIDDEN_ACTIONS
from ..connectors import catalog, registry
from ..connectors.base import ActionResult, CredentialHandle, FetchContext
from ..models import (
    Approval,
    ApprovalStatus,
    Connection,
    Message,
    RiskTier,
    Task,
    TaskStatus,
    User,
)
from . import audit

log = logging.getLogger("duet.executor")


class ExecutionRefused(Exception):
    """Raised when an approval cannot be executed. Never a partial write."""


def decide(
    db: Session,
    approval: Approval,
    *,
    approved: bool,
    decided_by: str = "user",
    note: str | None = None,
) -> tuple[Approval, ActionResult | None]:
    """Record the human decision and, on approval, execute immediately."""
    if approval.status != ApprovalStatus.PENDING.value:
        raise ExecutionRefused(f"This action was already {approval.status}.")

    now = datetime.now(timezone.utc)
    approval.decided_at = now
    approval.decided_by = decided_by
    approval.decision_note = note

    if not approved:
        approval.status = ApprovalStatus.REJECTED.value
        audit.record(
            db,
            action="approval.rejected",
            actor=f"user:{decided_by}",
            user_id=approval.user_id,
            resource_type="approval",
            resource_id=approval.id,
            detail={"action": approval.action_type, "note": note},
        )
        _note_in_thread(db, approval, "Rejected. Nothing was sent or changed.")
        _settle_task(db, approval)
        db.commit()
        return approval, None

    approval.status = ApprovalStatus.APPROVED.value
    audit.record(
        db,
        action="approval.approved",
        actor=f"user:{decided_by}",
        user_id=approval.user_id,
        resource_type="approval",
        resource_id=approval.id,
        detail={"action": approval.action_type, "connector": approval.connector_key},
    )
    db.flush()

    result = execute(db, approval)
    db.commit()
    return approval, result


def execute(db: Session, approval: Approval) -> ActionResult:
    """Perform the approved action. Caller must have set status to APPROVED."""
    try:
        _preflight(db, approval)
    except ExecutionRefused as exc:
        approval.status = ApprovalStatus.REJECTED.value
        approval.execution_result = {"ok": False, "error": str(exc)}
        audit.record(
            db,
            action="approval.refused_at_execution",
            actor="system",
            user_id=approval.user_id,
            resource_type="approval",
            resource_id=approval.id,
            outcome="refused",
            detail={"reason": str(exc)},
        )
        _note_in_thread(db, approval, f"I couldn't carry that out: {exc}")
        _settle_task(db, approval)
        return ActionResult(ok=False, summary=str(exc), error="refused")

    adapter = registry.resolve(approval.connector_key or "")
    conn = _connection(db, approval)
    ctx = FetchContext(
        user_id=approval.user_id,
        connection_id=conn.id if conn else "",
        since=None,
        cursor=conn.cursor if conn else {},
        credential=(
            CredentialHandle(ref=conn.credential_ref, connector_key=conn.connector_key)
            if conn and conn.credential_ref
            else None
        ),
    )

    try:
        result = adapter.execute(approval.action_type, approval.payload or {}, ctx)
    except Exception as exc:
        log.exception("execution failed for approval %s", approval.id)
        result = ActionResult(
            ok=False, summary=f"{type(exc).__name__}: {exc}", error="adapter_error"
        )

    approval.executed_at = datetime.now(timezone.utc)
    approval.execution_result = {
        "ok": result.ok,
        "summary": result.summary,
        "data": result.data,
        "external_id": result.external_id,
    }
    approval.revert_plan = result.revert_plan or {}
    approval.status = (
        ApprovalStatus.EXECUTED.value if result.ok else ApprovalStatus.APPROVED.value
    )

    audit.record(
        db,
        action="action.executed" if result.ok else "action.failed",
        actor="system",
        user_id=approval.user_id,
        resource_type="approval",
        resource_id=approval.id,
        outcome="ok" if result.ok else "error",
        detail={
            "action": approval.action_type,
            "connector": approval.connector_key,
            "summary": result.summary,
            "external_id": result.external_id,
            "reversible": approval.reversible,
        },
    )

    _note_in_thread(
        db,
        approval,
        result.summary if result.ok else f"That didn't go through: {result.summary}",
    )
    _settle_task(db, approval)
    return result


def revert(db: Session, approval: Approval, *, actor: str = "user") -> ActionResult:
    """Undo an executed action where the adapter left a revert plan."""
    if approval.status != ApprovalStatus.EXECUTED.value:
        raise ExecutionRefused("Only an executed action can be reverted.")
    if not approval.reversible or not approval.revert_plan:
        raise ExecutionRefused(
            f"{approval.action_type} can't be undone from here - it has to be handled "
            f"in {approval.connector_key or 'the app'} directly."
        )

    adapter = registry.resolve(approval.connector_key or "")
    conn = _connection(db, approval)
    ctx = FetchContext(
        user_id=approval.user_id,
        connection_id=conn.id if conn else "",
        since=None,
        cursor={},
    )

    plan = approval.revert_plan
    result = adapter.execute(plan.get("action", "revert"), plan, ctx)

    if result.ok:
        approval.status = ApprovalStatus.REVERTED.value

    audit.record(
        db,
        action="action.reverted",
        actor=f"user:{actor}",
        user_id=approval.user_id,
        resource_type="approval",
        resource_id=approval.id,
        outcome="ok" if result.ok else "error",
        detail={"action": approval.action_type, "summary": result.summary},
    )
    _note_in_thread(db, approval, f"Reverted: {result.summary}")
    db.commit()
    return result


def expire_stale(db: Session) -> int:
    """Approvals that sat too long are expired rather than silently executed later."""
    from sqlalchemy import select

    now = datetime.now(timezone.utc)
    stale = list(
        db.execute(
            select(Approval).where(
                Approval.status == ApprovalStatus.PENDING.value,
                Approval.expires_at.is_not(None),
                Approval.expires_at < now,
            )
        ).scalars()
    )
    for approval in stale:
        approval.status = ApprovalStatus.EXPIRED.value
        audit.record(
            db,
            action="approval.expired",
            actor="system",
            user_id=approval.user_id,
            resource_type="approval",
            resource_id=approval.id,
            detail={"action": approval.action_type},
        )
        _settle_task(db, approval)
    if stale:
        db.commit()
    return len(stale)


# --------------------------------------------------------------------------- #
def _preflight(db: Session, approval: Approval) -> None:
    if approval.action_type in FORBIDDEN_ACTIONS:
        raise ExecutionRefused(FORBIDDEN_ACTIONS[approval.action_type])

    if approval.connector_key:
        declared = catalog.risk_for(approval.connector_key, approval.action_type)
        if declared is RiskTier.FORBIDDEN:
            raise ExecutionRefused(
                f"{approval.connector_key}.{approval.action_type} is blocked by policy."
            )

        conn = _connection(db, approval)
        if conn is None:
            raise ExecutionRefused(f"{approval.connector_key} is no longer connected.")
        if not conn.enabled or conn.status != "connected":
            raise ExecutionRefused(f"{approval.connector_key} is disconnected or disabled.")

    if approval.expires_at:
        expires = approval.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            raise ExecutionRefused("This approval expired before it was acted on.")


def _connection(db: Session, approval: Approval) -> Connection | None:
    if not approval.connector_key:
        return None
    from sqlalchemy import select

    return db.execute(
        select(Connection).where(
            Connection.user_id == approval.user_id,
            Connection.connector_key == approval.connector_key,
        )
    ).scalar_one_or_none()


def _note_in_thread(db: Session, approval: Approval, text: str) -> None:
    db.add(
        Message(
            user_id=approval.user_id,
            role="assistant",
            kind="task_update",
            content=text,
            task_id=approval.task_id,
            approval_id=approval.id,
            meta={"action": approval.action_type},
        )
    )


def _settle_task(db: Session, approval: Approval) -> None:
    """Close the parent task once nothing is pending on it."""
    if not approval.task_id:
        return
    from sqlalchemy import select

    task = db.get(Task, approval.task_id)
    if task is None or task.status != TaskStatus.AWAITING_APPROVAL.value:
        return

    outstanding = db.execute(
        select(Approval).where(
            Approval.task_id == task.id,
            Approval.status == ApprovalStatus.PENDING.value,
        )
    ).first()
    if outstanding is None:
        task.status = TaskStatus.DONE.value
        task.finished_at = datetime.now(timezone.utc)
