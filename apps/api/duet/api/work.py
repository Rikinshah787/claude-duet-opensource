"""Tasks, approvals and signals - what the assistant is doing and what it wants.

The approval routes are the ones that matter: they are the human gate, and they
are deliberately the only way an action reaches the executor.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..core import executor
from ..core.executor import ExecutionRefused
from ..core.orchestrator import create_task
from ..models import Approval, Signal, Task, TaskStep
from .deps import ActorDep, DbDep, UserDep

router = APIRouter(tags=["work"])


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #
class NewTask(BaseModel):
    intent: str = Field(min_length=1, max_length=8000)
    title: str | None = None


def _serialize_task(t: Task) -> dict[str, Any]:
    return {
        "id": t.id,
        "title": t.title,
        "intent": t.intent,
        "status": t.status,
        "origin": t.origin,
        "lead_agent": t.lead_agent,
        "plan": t.plan or [],
        "artifacts": t.artifacts or [],
        "error": t.error,
        "attempts": t.attempts,
        "created_at": t.created_at.isoformat(),
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "finished_at": t.finished_at.isoformat() if t.finished_at else None,
    }


def _serialize_step(s: TaskStep) -> dict[str, Any]:
    return {
        "id": s.id,
        "idx": s.idx,
        "agent": s.agent,
        "tool": s.tool,
        "summary": s.summary,
        "status": s.status,
        "output": s.output or {},
        "error": s.error,
        "duration_ms": s.duration_ms,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "finished_at": s.finished_at.isoformat() if s.finished_at else None,
    }


@router.get("/tasks")
def list_tasks(db: DbDep, user: UserDep, status: str | None = None, limit: int = 50) -> dict:
    stmt = select(Task).where(Task.user_id == user.id)
    if status:
        stmt = stmt.where(Task.status == status)
    rows = list(
        db.execute(stmt.order_by(Task.created_at.desc()).limit(min(limit, 200))).scalars()
    )
    return {"tasks": [_serialize_task(t) for t in rows]}


@router.post("/tasks", status_code=201)
def post_task(payload: NewTask, db: DbDep, user: ActorDep) -> dict:
    task = create_task(db, user, payload.intent, origin="user", title=payload.title)
    return {"task": _serialize_task(task)}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, db: DbDep, user: UserDep) -> dict:
    task = db.get(Task, task_id)
    if task is None or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="No such task")

    steps = list(
        db.execute(
            select(TaskStep).where(TaskStep.task_id == task.id).order_by(TaskStep.idx.asc())
        ).scalars()
    )
    approvals = list(
        db.execute(select(Approval).where(Approval.task_id == task.id)).scalars()
    )
    return {
        "task": _serialize_task(task),
        "steps": [_serialize_step(s) for s in steps],
        "approvals": [_serialize_approval(a) for a in approvals],
    }


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, db: DbDep, user: ActorDep) -> dict:
    from ..models import TaskStatus

    task = db.get(Task, task_id)
    if task is None or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="No such task")
    if task.status in {TaskStatus.DONE.value, TaskStatus.FAILED.value}:
        raise HTTPException(status_code=409, detail="That task already finished.")

    task.status = TaskStatus.CANCELLED.value
    db.commit()
    return {"task": _serialize_task(task)}


# --------------------------------------------------------------------------- #
# Approvals
# --------------------------------------------------------------------------- #
class Decision(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


def _serialize_approval(a: Approval) -> dict[str, Any]:
    return {
        "id": a.id,
        "task_id": a.task_id,
        "title": a.title,
        "rationale": a.rationale,
        "action_type": a.action_type,
        "connector_key": a.connector_key,
        "risk_tier": a.risk_tier,
        "preview": a.preview or {},
        "payload": a.payload or {},
        "status": a.status,
        "reversible": a.reversible,
        "decision_note": a.decision_note,
        "execution_result": a.execution_result or {},
        "created_at": a.created_at.isoformat(),
        "decided_at": a.decided_at.isoformat() if a.decided_at else None,
        "executed_at": a.executed_at.isoformat() if a.executed_at else None,
        "expires_at": a.expires_at.isoformat() if a.expires_at else None,
    }


@router.get("/approvals")
def list_approvals(db: DbDep, user: UserDep, status: str | None = None, limit: int = 50) -> dict:
    stmt = select(Approval).where(Approval.user_id == user.id)
    if status:
        stmt = stmt.where(Approval.status == status)
    rows = list(
        db.execute(stmt.order_by(Approval.created_at.desc()).limit(min(limit, 200))).scalars()
    )
    return {"approvals": [_serialize_approval(a) for a in rows]}


def _load_approval(db, user, approval_id: str) -> Approval:
    approval = db.get(Approval, approval_id)
    if approval is None or approval.user_id != user.id:
        raise HTTPException(status_code=404, detail="No such approval")
    return approval


@router.post("/approvals/{approval_id}/approve")
def approve(approval_id: str, db: DbDep, user: ActorDep, payload: Decision | None = None) -> dict:
    approval = _load_approval(db, user, approval_id)
    try:
        approval, result = executor.decide(
            db,
            approval,
            approved=True,
            decided_by=user.email,
            note=payload.note if payload else None,
        )
    except ExecutionRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "approval": _serialize_approval(approval),
        "result": {
            "ok": result.ok if result else False,
            "summary": result.summary if result else "",
            "data": result.data if result else {},
        },
    }


@router.post("/approvals/{approval_id}/reject")
def reject(approval_id: str, db: DbDep, user: ActorDep, payload: Decision | None = None) -> dict:
    approval = _load_approval(db, user, approval_id)
    try:
        approval, _ = executor.decide(
            db,
            approval,
            approved=False,
            decided_by=user.email,
            note=payload.note if payload else None,
        )
    except ExecutionRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"approval": _serialize_approval(approval)}


@router.post("/approvals/{approval_id}/revert")
def revert(approval_id: str, db: DbDep, user: ActorDep) -> dict:
    approval = _load_approval(db, user, approval_id)
    try:
        result = executor.revert(db, approval, actor=user.email)
    except ExecutionRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "approval": _serialize_approval(approval),
        "result": {"ok": result.ok, "summary": result.summary},
    }


# --------------------------------------------------------------------------- #
# Signals
# --------------------------------------------------------------------------- #
def _serialize_signal(s: Signal) -> dict[str, Any]:
    return {
        "id": s.id,
        "title": s.title,
        "body": s.body,
        "actor": s.actor,
        "connector_key": s.connector_key,
        "kind": s.kind,
        "score": round(s.score, 1),
        "disposition": s.disposition,
        "state": s.state,
        "reasons": s.reasons or [],
        "factors": s.factors or {},
        "url": s.url,
        "occurred_at": s.occurred_at.isoformat(),
    }


@router.get("/signals")
def list_signals(
    db: DbDep,
    user: UserDep,
    disposition: str | None = None,
    state: str | None = None,
    limit: int = 100,
) -> dict:
    stmt = select(Signal).where(Signal.user_id == user.id)
    if disposition:
        stmt = stmt.where(Signal.disposition == disposition)
    if state:
        stmt = stmt.where(Signal.state == state)
    rows = list(
        db.execute(stmt.order_by(Signal.score.desc()).limit(min(limit, 400))).scalars()
    )
    return {"signals": [_serialize_signal(s) for s in rows]}


@router.get("/signals/{signal_id}")
def get_signal(signal_id: str, db: DbDep, user: UserDep) -> dict:
    s = db.get(Signal, signal_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(status_code=404, detail="No such signal")
    return {"signal": _serialize_signal(s)}


@router.post("/signals/{signal_id}/dismiss")
def dismiss_signal(signal_id: str, db: DbDep, user: ActorDep) -> dict:
    """Dismissing teaches the priority engine - see `historical_factor`."""
    s = db.get(Signal, signal_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(status_code=404, detail="No such signal")
    s.state = "dismissed"
    db.commit()
    return {"ok": True, "signal": _serialize_signal(s)}


@router.post("/signals/{signal_id}/handle")
def handle_signal(signal_id: str, db: DbDep, user: ActorDep) -> dict:
    s = db.get(Signal, signal_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(status_code=404, detail="No such signal")
    s.state = "handled"
    db.commit()
    return {"ok": True, "signal": _serialize_signal(s)}


@router.post("/signals/{signal_id}/act")
def act_on_signal(signal_id: str, db: DbDep, user: ActorDep) -> dict:
    """Turn a signal into a task - 'deal with this one'."""
    s = db.get(Signal, signal_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(status_code=404, detail="No such signal")

    task = create_task(
        db,
        user,
        f"Handle this: {s.title}" + (f"\n\n{s.body}" if s.body else ""),
        origin="user",
        signal_id=s.id,
        title=f"Handle: {s.title}",
    )
    s.state = "handled"
    db.commit()
    return {"task": _serialize_task(task)}
