"""Observability.

Three things an operator of this system needs to see, and none of them is
"requests per second":

* **What did the agents actually do?** Every agent turn is already a persisted
  `TaskStep`, which makes the trace queryable rather than ephemeral. The
  aggregation here turns those rows into per-agent latency and error rates.
* **What is it costing?** The sweep runs unattended, so model turns accumulate
  whether or not anyone is watching. Cost is estimated from recorded token
  counts and projected forward at the current sweep cadence, because the useful
  question is "what will this cost me per month", not "how many tokens so far".
* **Is the autonomy healthy?** Sweep success, stale connectors, approvals
  sitting unanswered, tasks stuck retrying.

OpenTelemetry is wired in when the SDK is installed and is a no-op otherwise.
Making it optional keeps the local install dependency-free while leaving a real
export path for deployment.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Approval,
    ApprovalStatus,
    AuditLog,
    Connection,
    Signal,
    Task,
    TaskStatus,
    TaskStep,
)

log = logging.getLogger("duet.telemetry")

# Rough per-million-token rates for local models. Electricity and depreciation,
# not an API bill - the point is to make unattended usage visible, not exact.
COST_PER_MTOK_IN = 0.08
COST_PER_MTOK_OUT = 0.24


def instrument(app) -> bool:
    """Attach OpenTelemetry if the SDK is present. Returns whether it engaged."""
    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.debug("opentelemetry not installed; tracing disabled")
        return False

    try:
        provider = TracerProvider(
            resource=Resource.create({SERVICE_NAME: "duet-api"})
        )
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        except ImportError:
            pass  # tracer still useful without an exporter configured

        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        log.info("opentelemetry instrumentation enabled")
        return True
    except Exception as exc:
        log.warning("opentelemetry setup failed: %s", exc)
        return False


# --------------------------------------------------------------------------- #
def collect(db: Session, user_id: str | None = None) -> dict[str, Any]:
    """Everything the Agent Health view renders, in one query pass."""
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)

    def scoped(stmt, model):
        return stmt.where(model.user_id == user_id) if user_id else stmt

    # --- agents ---
    rows = db.execute(
        select(
            TaskStep.agent,
            func.count(TaskStep.id),
            func.avg(TaskStep.duration_ms),
            func.max(TaskStep.duration_ms),
        ).group_by(TaskStep.agent)
    ).all()
    errors = dict(
        db.execute(
            select(TaskStep.agent, func.count(TaskStep.id))
            .where(TaskStep.status == "error")
            .group_by(TaskStep.agent)
        ).all()
    )
    agents = [
        {
            "agent": agent,
            "runs": runs,
            "errors": errors.get(agent, 0),
            "error_rate": round(errors.get(agent, 0) / runs, 3) if runs else 0.0,
            "avg_ms": int(avg or 0),
            "max_ms": int(mx or 0),
        }
        for agent, runs, avg, mx in rows
    ]

    # --- tasks ---
    task_counts = dict(
        db.execute(
            scoped(select(Task.status, func.count(Task.id)), Task).group_by(Task.status)
        ).all()
    )
    tokens_in, tokens_out = db.execute(
        scoped(select(func.sum(Task.tokens_in), func.sum(Task.tokens_out)), Task)
    ).one()
    tokens_in = int(tokens_in or 0)
    tokens_out = int(tokens_out or 0)

    # --- signals ---
    signal_counts = dict(
        db.execute(
            scoped(
                select(Signal.disposition, func.count(Signal.id)), Signal
            ).group_by(Signal.disposition)
        ).all()
    )
    recent_signals = db.execute(
        scoped(
            select(func.count(Signal.id)).where(Signal.created_at >= day_ago), Signal
        )
    ).scalar_one()

    # --- approvals ---
    approval_counts = dict(
        db.execute(
            scoped(
                select(Approval.status, func.count(Approval.id)), Approval
            ).group_by(Approval.status)
        ).all()
    )
    oldest_pending = db.execute(
        scoped(
            select(func.min(Approval.created_at)).where(
                Approval.status == ApprovalStatus.PENDING.value
            ),
            Approval,
        )
    ).scalar_one()

    # --- connectors ---
    stale_cutoff = now - timedelta(seconds=settings.sweep_interval_s * 4)
    connections = list(
        db.execute(scoped(select(Connection), Connection)).scalars()
    )
    stale = [
        c.connector_key
        for c in connections
        if c.enabled
        and c.monitored
        and (c.last_sync_at is None or c.last_sync_at.replace(tzinfo=timezone.utc) < stale_cutoff)
    ]

    sweeps_24h = db.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.action == "monitor.sweep", AuditLog.at >= day_ago
        )
    ).scalar_one()
    injections = db.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.action == "security.injection_detected"
        )
    ).scalar_one()

    return {
        "generated_at": now.isoformat(),
        "agents": sorted(agents, key=lambda a: a["runs"], reverse=True),
        "tasks": {
            "by_status": task_counts,
            "stuck": task_counts.get(TaskStatus.FAILED.value, 0),
            "in_flight": task_counts.get(TaskStatus.RUNNING.value, 0)
            + task_counts.get(TaskStatus.QUEUED.value, 0),
        },
        "signals": {
            "by_disposition": signal_counts,
            "last_24h": recent_signals,
            "noise_ratio": _noise_ratio(signal_counts),
        },
        "approvals": {
            "by_status": approval_counts,
            "pending": approval_counts.get(ApprovalStatus.PENDING.value, 0),
            "oldest_pending_age_h": (
                round((now - oldest_pending.replace(tzinfo=timezone.utc)).total_seconds() / 3600, 1)
                if oldest_pending
                else None
            ),
        },
        "connectors": {
            "total": len(connections),
            "healthy": sum(1 for c in connections if c.status == "connected"),
            "errored": [c.connector_key for c in connections if c.status == "error"],
            "stale": stale,
        },
        "security": {"injections_detected": injections},
        "autonomy": {"sweeps_last_24h": sweeps_24h},
        "cost": _cost(tokens_in, tokens_out, sweeps_24h),
    }


def _noise_ratio(by_disposition: dict[str, int]) -> float:
    """Share of observations the engine chose not to surface.

    A healthy value is high. If this trends toward zero the engine has stopped
    filtering and the briefing is on its way to becoming another inbox.
    """
    total = sum(by_disposition.values())
    if not total:
        return 0.0
    quiet = by_disposition.get("ignore", 0) + by_disposition.get("defer", 0)
    return round(quiet / total, 3)


def _cost(tokens_in: int, tokens_out: int, sweeps_24h: int) -> dict[str, Any]:
    spent = (tokens_in / 1_000_000) * COST_PER_MTOK_IN + (
        tokens_out / 1_000_000
    ) * COST_PER_MTOK_OUT

    sweeps_per_day = 86_400 / max(1, settings.sweep_interval_s)

    return {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "estimated_spend_usd": round(spent, 4),
        "sweeps_per_day_at_current_cadence": round(sweeps_per_day, 1),
        # Deliberately not "tokens per sweep". Sweeping and scoring use no model
        # turns at all - every token above was spent running a task. Dividing
        # task spend by sweep count would invent a per-sweep cost that does not
        # exist and would make lengthening the interval look like a saving.
        "token_cost_driver": "tasks",
        "note": (
            f"Sweeps run {sweeps_per_day:.0f}x/day and cost vendor API calls, "
            "not model turns. Model spend above comes from tasks you ran."
        ),
    }


def prometheus(metrics: dict[str, Any]) -> str:
    """Expose the same numbers in Prometheus text format."""
    lines: list[str] = []

    def emit(name: str, value: Any, labels: str = "", help_text: str = "") -> None:
        if not isinstance(value, (int, float)):
            return
        if help_text:
            lines.append(f"# HELP duet_{name} {help_text}")
            lines.append(f"# TYPE duet_{name} gauge")
        lines.append(f"duet_{name}{labels} {value}")

    for agent in metrics["agents"]:
        label = f'{{agent="{agent["agent"]}"}}'
        emit("agent_runs_total", agent["runs"], label, "Agent turns executed")
        emit("agent_errors_total", agent["errors"], label)
        emit("agent_duration_ms_avg", agent["avg_ms"], label)

    for status, count in metrics["tasks"]["by_status"].items():
        emit("tasks", count, f'{{status="{status}"}}', "Tasks by status")
    for disposition, count in metrics["signals"]["by_disposition"].items():
        emit("signals", count, f'{{disposition="{disposition}"}}', "Signals by disposition")

    emit("approvals_pending", metrics["approvals"]["pending"], "", "Approvals awaiting a human")
    emit("connectors_healthy", metrics["connectors"]["healthy"], "", "Connected apps healthy")
    emit("connectors_stale", len(metrics["connectors"]["stale"]))
    emit("signal_noise_ratio", metrics["signals"]["noise_ratio"], "", "Share filtered out")
    emit("injections_detected_total", metrics["security"]["injections_detected"])
    emit("tokens_in_total", metrics["cost"]["tokens_in"])
    emit("tokens_out_total", metrics["cost"]["tokens_out"])

    return "\n".join(lines) + "\n"
