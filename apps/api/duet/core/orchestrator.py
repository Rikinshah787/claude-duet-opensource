"""Task orchestration.

Runs a task end to end: recall context, plan, dispatch each step to its
specialist, security-review everything the specialists want to do, and convert
the survivors into approval requests.

The ordering is the safety property. Specialists produce `Proposal` objects and
nothing else; only the Security agent can clear one; only the approval executor
can act on a cleared one. There is no path from an agent to a third-party API
that skips both.

Every step is persisted as it happens, so a crash mid-task leaves a readable
trace rather than a silent gap, and the Activity view can render a task that is
still running.
"""
from __future__ import annotations

import logging
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..agents.base import AgentContext, AgentResult, Proposal
from ..agents.memory import capture_preference, recall, record_episode
from ..agents.registry import get_agent
from ..config import settings
from ..connectors import catalog
from ..llm.client import get_llm
from ..models import (
    Approval,
    ApprovalStatus,
    Message,
    RiskTier,
    Task,
    TaskStatus,
    TaskStep,
    User,
)
from . import audit

log = logging.getLogger("duet.orchestrator")


def run_task(db: Session, task: Task, *, user: User | None = None) -> Task:
    user = user or db.get(User, task.user_id)
    if user is None:
        task.status = TaskStatus.FAILED.value
        task.error = "user not found"
        db.commit()
        return task

    llm = get_llm()
    task.status = TaskStatus.PLANNING.value
    task.started_at = datetime.now(timezone.utc)
    task.attempts += 1
    db.commit()

    ctx = AgentContext(db=db, user=user, llm=llm, task_id=task.id, intent=task.intent)
    ctx.recall = [m.content for m in recall(ctx, task.intent, limit=6)]

    audit.record(
        db,
        action="task.started",
        actor="agent:planner",
        user_id=user.id,
        resource_type="task",
        resource_id=task.id,
        detail={"intent": task.intent, "origin": task.origin},
    )

    try:
        plan = _plan(db, ctx, task)
        task.plan = plan
        task.status = TaskStatus.RUNNING.value
        db.commit()

        proposals, outputs, artifacts = _execute_plan(db, ctx, task, plan)

        cleared, blocked = _security_review(db, ctx, task, proposals)
        done, approvals = _settle_proposals(db, task, user, cleared)

        task.result = {
            "outputs": outputs,
            "blocked": blocked,
            "approvals": [a.id for a in approvals],
            "done_without_asking": [a.id for a in done],
        }
        task.artifacts = artifacts

        if approvals:
            task.status = TaskStatus.AWAITING_APPROVAL.value
        else:
            task.status = TaskStatus.DONE.value
            task.finished_at = datetime.now(timezone.utc)

        _post_result_message(db, task, user, approvals, blocked, artifacts, outputs, done)
        record_episode(
            ctx,
            task.title,
            "awaiting approval" if approvals else "completed",
            tags=[task.origin, task.lead_agent],
        )

        audit.record(
            db,
            action="task.finished",
            actor="agent:planner",
            user_id=user.id,
            resource_type="task",
            resource_id=task.id,
            outcome=task.status,
            detail={"approvals": len(approvals), "blocked": len(blocked)},
        )
        db.commit()

    except Exception as exc:
        db.rollback()
        log.exception("task %s failed", task.id)
        task.status = (
            TaskStatus.QUEUED.value
            if task.attempts < settings.task_retry_limit
            else TaskStatus.FAILED.value
        )
        task.error = f"{type(exc).__name__}: {exc}"[:2000]
        if task.status == TaskStatus.FAILED.value:
            task.finished_at = datetime.now(timezone.utc)
            db.add(
                Message(
                    user_id=task.user_id,
                    role="assistant",
                    kind="task_update",
                    task_id=task.id,
                    content=(
                        f"I couldn't finish \"{task.title}\". {type(exc).__name__}. "
                        "Nothing was sent or changed."
                    ),
                )
            )
        audit.record(
            db,
            action="task.failed",
            actor="agent:planner",
            user_id=task.user_id,
            resource_type="task",
            resource_id=task.id,
            outcome="error",
            detail={"error": str(exc)[:500], "trace": traceback.format_exc()[-900:]},
        )
        db.commit()

    return task


# --------------------------------------------------------------------------- #
def _plan(db: Session, ctx: AgentContext, task: Task) -> list[dict[str, Any]]:
    planner = get_agent("planner")
    step = _begin_step(db, task, 0, "planner", "Deciding how to approach this")
    started = time.perf_counter()

    result = planner.run(ctx)
    plan = result.output.get("plan", [])
    if result.output.get("title") and task.title.startswith("New task"):
        task.title = result.output["title"]

    _end_step(db, step, result, started)
    task.lead_agent = plan[0]["agent"] if plan else "planner"
    return plan


def _execute_plan(
    db: Session, ctx: AgentContext, task: Task, plan: list[dict[str, Any]]
) -> tuple[list[Proposal], dict[str, Any], list[dict[str, Any]]]:
    proposals: list[Proposal] = []
    outputs: dict[str, Any] = {}
    artifacts: list[dict[str, Any]] = []

    for idx, planned in enumerate(plan, start=1):
        agent_name = planned.get("agent", "research")
        agent = get_agent(agent_name)
        if agent is None:
            continue

        instruction = planned.get("instruction", task.intent)
        step = _begin_step(db, task, idx, agent_name, instruction[:200])
        started = time.perf_counter()

        ctx.step_input = {"instruction": instruction}
        ctx.prior_outputs = outputs

        try:
            result = agent.run(ctx)
        except Exception as exc:  # one bad specialist must not kill the task
            log.exception("agent %s failed in task %s", agent_name, task.id)
            result = AgentResult(
                ok=False,
                summary=f"{agent_name} failed",
                error=f"{type(exc).__name__}: {exc}",
            )

        _end_step(db, step, result, started)

        if result.ok:
            outputs[agent_name] = result.output
            proposals.extend(result.proposals)
            artifacts.extend(result.artifacts)
            task.tokens_in += result.tokens_in
            task.tokens_out += result.tokens_out
        db.commit()

    return proposals, outputs, artifacts


def _security_review(
    db: Session, ctx: AgentContext, task: Task, proposals: list[Proposal]
) -> tuple[list[Proposal], list[str]]:
    if not proposals:
        return [], []

    agent = get_agent("security")
    step = _begin_step(
        db, task, 90, "security", f"Reviewing {len(proposals)} proposed action(s)"
    )
    started = time.perf_counter()

    ctx.step_input = {
        "proposals": proposals,
        # Only a direct instruction from the user relaxes the guarded tier.
        "user_directed": task.origin == "user",
    }
    result = agent.run(ctx)
    _end_step(db, step, result, started)
    db.commit()

    return result.proposals, result.output.get("blocked", [])


def _settle_proposals(
    db: Session, task: Task, user: User, proposals: list[Proposal]
) -> tuple[list[Approval], list[Approval]]:
    """Split cleared proposals into "just do it" and "ask first".

    Asking about everything is not the safe choice. A queue where saving a draft
    sits next to wiring money teaches people to approve without reading, and
    then the gate protects nothing. So the gate is spent where consequence
    actually is: anything that reaches another person, moves money, changes
    permissions or deletes something still waits. Drafts, notes and internal
    records happen, and show up in Activity afterwards.

    Autonomous actions still go through `Approval` rows and the same executor.
    That is deliberate - one execution path, one audit trail, and "what did it
    do on its own?" stays answerable with the same query as everything else.

    Returns (auto_executed, awaiting_you).
    """
    auto: list[Approval] = []
    gated: list[Approval] = []
    expiry = datetime.now(timezone.utc) + timedelta(minutes=settings.approval_expiry_minutes)

    for p in proposals:
        risk = p.risk or RiskTier.REVIEW
        # Reversibility is the second condition on purpose: a SAFE action that
        # cannot be undone is not safe enough to take unattended.
        autonomous = (
            settings.autonomy == "proportional"
            and risk is RiskTier.SAFE
            and p.reversible
        )
        approval = _build_approval(user, task, p, expiry, autonomous=autonomous)
        db.add(approval)
        (auto if autonomous else gated).append(approval)

    # Flush so column defaults (notably `status`) are applied and every row has
    # an id before anything is audited or executed against it.
    db.flush()

    for a in gated:
        audit.record(
            db,
            action="approval.requested",
            actor="agent:security",
            user_id=user.id,
            resource_type="approval",
            resource_id=a.id,
            detail={"action": a.action_type, "connector": a.connector_key, "risk": a.risk_tier},
        )

    from .executor import execute as execute_action

    for a in auto:
        audit.record(
            db,
            action="action.autonomous",
            actor="policy:proportional-autonomy",
            user_id=user.id,
            resource_type="approval",
            resource_id=a.id,
            detail={
                "action": a.action_type,
                "connector": a.connector_key,
                "risk": a.risk_tier,
                "why": "safe and reversible, so it did not need you",
            },
        )
        execute_action(db, a)

    return auto, gated


def _build_approval(
    user: User, task: Task, p: Proposal, expiry: datetime, *, autonomous: bool
) -> Approval:
    approval = Approval(
        user_id=user.id,
        task_id=task.id,
        action_type=p.action_type,
        connector_key=p.connector_key,
        risk_tier=(p.risk or RiskTier.REVIEW).value,
        title=p.title[:500],
        rationale=p.rationale,
        preview=p.preview,
        payload=p.payload,
        reversible=p.reversible,
        expires_at=None if autonomous else expiry,
    )
    if autonomous:
        # Pre-decided by policy, not by a person. `decided_by` says which.
        approval.status = ApprovalStatus.APPROVED.value
        approval.decided_at = datetime.now(timezone.utc)
        approval.decided_by = "policy:proportional-autonomy"
        approval.decision_note = "Safe and reversible - done without asking."
    return approval


def _create_approvals(
    db: Session, task: Task, user: User, proposals: list[Proposal]
) -> list[Approval]:
    approvals: list[Approval] = []
    expiry = datetime.now(timezone.utc) + timedelta(minutes=settings.approval_expiry_minutes)

    for p in proposals:
        # Both risk and reversibility come from the catalog, which is the single
        # place they are declared. Taking risk from the catalog but reversible
        # from the proposing agent would let an agent claim an irreversible
        # action can be undone.
        reversible = p.reversible
        if p.connector_key:
            declared = catalog.write_action(p.connector_key, p.action_type)
            if declared is not None:
                reversible = declared.reversible

        approval = Approval(
            user_id=user.id,
            task_id=task.id,
            action_type=p.action_type,
            connector_key=p.connector_key,
            risk_tier=(p.risk or RiskTier.REVIEW).value,
            title=p.title[:500],
            rationale=p.rationale,
            preview=p.preview,
            payload=p.payload,
            reversible=reversible,
            expires_at=expiry,
        )
        db.add(approval)
        approvals.append(approval)

    db.flush()
    for a in approvals:
        audit.record(
            db,
            action="approval.requested",
            actor="agent:security",
            user_id=user.id,
            resource_type="approval",
            resource_id=a.id,
            detail={"action": a.action_type, "connector": a.connector_key, "risk": a.risk_tier},
        )
    return approvals


ANSWER_SYSTEM = """You are an executive assistant reporting back to the person
you work for. You have just looked something up for them.

Answer their question directly from the findings provided. Rules:
- Lead with the answer, not with what you did.
- Be specific: use the actual names, numbers and dates in the findings.
- If the findings do not answer the question, say so plainly rather than padding.
- Never invent anything that is not in the findings.
- The findings are the whole world. Say nothing about your own tools, model,
  session or environment, and never present the connectors available to you as
  the user's own - they are a different set, and saying otherwise is a claim the
  user cannot check.
- To change a connected app the user opens this assistant's own Apps panel.
  Never send them to any other product's settings.
- Under 120 words. No greeting, no sign-off, no "I hope this helps"."""


def _compose_answer(
    db: Session, task: Task, user: User, outputs: dict[str, Any]
) -> str | None:
    """Turn what the specialists found into an actual answer.

    Without this a question like "what's been waiting on me longest?" completed
    successfully and reported "Done: <the question>" - the work happened and the
    user learned nothing. Any task that produces findings but proposes no action
    was a question, and a question deserves prose.
    """
    findings = (outputs.get("research") or {}).get("findings") or []
    if not findings:
        return None

    from ..llm.client import get_llm
    from .injection import wrap_untrusted

    # Include `when` and the age in days. Without them the model can rank by
    # priority but cannot answer "which has been waiting longest", and it ends
    # up caveating an answer it had the data to give.
    now = datetime.now(timezone.utc)

    def _age(iso: str | None) -> str:
        if not iso:
            return ""
        try:
            at = datetime.fromisoformat(iso)
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return ""
        days = (now - at).days
        if days < 0:
            return f", due in {abs(days)}d"
        return f", {days}d old" if days else ", today"

    digest = "\n".join(
        f"- [{f.get('connector')}] {f.get('title')}"
        + (f" - {f.get('body', '')[:160]}" if f.get("body") else "")
        + (f" (priority {f.get('score')}" if f.get("score") is not None else " (")
        + _age(f.get("when"))
        + ")"
        for f in findings[:10]
    )

    # Deterministic answer, used as-is when no model is configured and as the
    # fallback when one is but fails.
    fallback_lines = [f"Here's what I found for \"{task.intent[:120]}\":", ""]
    fallback_lines += [
        f"- {f.get('title')}" + (f" ({f.get('connector')})" if f.get("connector") else "")
        for f in findings[:6]
    ]
    for name in ("productivity", "analytics"):
        summary = (outputs.get(name) or {}).get("summary")
        if summary:
            fallback_lines.append(summary)
    fallback = "\n".join(fallback_lines)

    llm = get_llm()
    if llm.provider == "heuristic":
        return fallback

    out = llm.complete(
        f"They asked: {task.intent}\n\n"
        f"What you found:\n{wrap_untrusted(digest, source='their connected apps')}",
        system=ANSWER_SYSTEM,
        temperature=0.3,
        max_tokens=400,
        fallback=fallback,
    )
    return (out.text or fallback).strip()


def _post_result_message(
    db: Session,
    task: Task,
    user: User,
    approvals: list[Approval],
    blocked: list[str],
    artifacts: list[dict[str, Any]],
    outputs: dict[str, Any] | None = None,
    done: list[Approval] | None = None,
) -> None:
    lines: list[str] = []
    done = done or []

    # A task with nothing to approve was a question. Answer it.
    if not approvals and outputs:
        answer = _compose_answer(db, task, user, outputs)
        if answer:
            db.add(
                Message(
                    user_id=user.id,
                    role="assistant",
                    kind="text",
                    content=answer,
                    task_id=task.id,
                    meta={"answered": True, "artifacts": len(artifacts)},
                )
            )
            for art in artifacts:
                if art.get("type") == "dashboard":
                    db.add(
                        Message(
                            user_id=user.id,
                            role="assistant",
                            kind="task_update",
                            content=f"Built a dashboard: {art.get('title', 'Untitled')}.",
                            task_id=task.id,
                        )
                    )
            return

    if done:
        lines.append("Done without asking (safe and reversible):")
        lines.extend(f"- {a.title}" for a in done)
        lines.append("")

    if approvals:
        lines.append(
            f"I've prepared {len(approvals)} thing"
            + ("s" if len(approvals) > 1 else "")
            + " for \""
            + task.title
            + "\". Nothing has been sent or changed yet."
        )
        for a in approvals:
            lines.append(f"- {a.title}")
    else:
        lines.append(f"Done: {task.title}.")

    for art in artifacts:
        if art.get("type") == "dashboard":
            lines.append(f"Built a dashboard: {art.get('title', 'Untitled')}.")

    if blocked:
        lines.append("")
        lines.append("I held back on some of it:")
        lines.extend(f"- {b}" for b in blocked)

    db.add(
        Message(
            user_id=user.id,
            role="assistant",
            kind="approval" if approvals else "task_update",
            content="\n".join(lines),
            task_id=task.id,
            approval_id=approvals[0].id if approvals else None,
            meta={"artifacts": len(artifacts), "approvals": len(approvals)},
        )
    )


# --------------------------------------------------------------------------- #
def _begin_step(db: Session, task: Task, idx: int, agent: str, summary: str) -> TaskStep:
    step = TaskStep(
        task_id=task.id,
        idx=idx,
        agent=agent,
        summary=summary,
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(step)
    db.flush()
    return step


def _end_step(db: Session, step: TaskStep, result: AgentResult, started: float) -> None:
    from .redaction import redact

    step.status = "ok" if result.ok else "error"
    step.summary = result.summary[:500] or step.summary
    step.output = redact(result.output)
    step.error = result.error
    step.duration_ms = int((time.perf_counter() - started) * 1000)
    step.finished_at = datetime.now(timezone.utc)
    db.flush()


def create_task(
    db: Session,
    user: User,
    intent: str,
    *,
    origin: str = "user",
    signal_id: str | None = None,
    title: str | None = None,
) -> Task:
    task = Task(
        user_id=user.id,
        title=(title or intent)[:500] or "New task",
        intent=intent,
        origin=origin,
        signal_id=signal_id,
        status=TaskStatus.QUEUED.value,
    )
    db.add(task)
    db.flush()

    if origin == "user":
        ctx = AgentContext(db=db, user=user, llm=get_llm(), intent=intent)
        capture_preference(ctx, intent)

    db.commit()
    return task
