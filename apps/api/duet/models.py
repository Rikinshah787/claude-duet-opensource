"""Domain model for the assistant.

The shape follows the product: things the assistant *noticed* (Signal), things it
*decided to do* (Task/TaskStep), things it *must ask about* (Approval), what it
*remembers* (Memory), and an append-only record of *everything it did* (AuditLog).
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class Disposition(str, enum.Enum):
    """What the priority engine decided to do with a signal."""

    NOTIFY_NOW = "notify_now"
    BRIEFING = "briefing"
    DEFER = "defer"
    IGNORE = "ignore"


class TaskStatus(str, enum.Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"
    REVERTED = "reverted"


class RiskTier(str, enum.Enum):
    """Drives the approval gate. FORBIDDEN cannot be unlocked by user consent."""

    SAFE = "safe"            # read-only, drafts, internal analysis
    REVIEW = "review"        # send a message, create a ticket, publish
    SENSITIVE = "sensitive"  # money, signatures, deletion, permissions
    FORBIDDEN = "forbidden"  # securities trading, inter-account transfers


class MemoryKind(str, enum.Enum):
    SHORT = "short"
    LONG = "long"
    SEMANTIC = "semantic"
    EPISODIC = "episodic"


# --------------------------------------------------------------------------- #
# Identity and connections
# --------------------------------------------------------------------------- #
class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    role: Mapped[str] = mapped_column(String(32), default="owner")  # RBAC: owner|member|viewer
    quiet_hours: Mapped[dict] = mapped_column(JSON, default=lambda: {"start": 21, "end": 7})
    briefing_optin: Mapped[bool] = mapped_column(Boolean, default=True)
    # Real figures the user supplies: starting_cash, monthly_burn,
    # ar_collection_rate. Empty by default, and the forecast refuses to project
    # rather than inventing a cash position - see AnalyticsAgent._runway.
    finance: Mapped[dict] = mapped_column(JSON, default=dict)

    connections: Mapped[list["Connection"]] = relationship(back_populates="user")


class Connection(Base, TimestampMixin):
    """A live link to one third-party app for one user."""

    __tablename__ = "connections"
    __table_args__ = (UniqueConstraint("user_id", "connector_key", name="uq_user_connector"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    connector_key: Mapped[str] = mapped_column(String(64), index=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(32), default="connected")  # connected|error|revoked
    account_label: Mapped[str] = mapped_column(String(200), default="")
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    # Never the credential itself - a pointer into the vault. See core/vault.py.
    credential_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    monitored: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor: Mapped[dict] = mapped_column(JSON, default=dict)  # incremental sync position

    user: Mapped[User] = relationship(back_populates="connections")


# --------------------------------------------------------------------------- #
# What the assistant noticed
# --------------------------------------------------------------------------- #
class Signal(Base, TimestampMixin):
    """One observed, scored event from a connected app."""

    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint("connection_id", "external_id", name="uq_conn_external"),
        Index("ix_signal_triage", "user_id", "disposition", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    connection_id: Mapped[str | None] = mapped_column(
        ForeignKey("connections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    connector_key: Mapped[str] = mapped_column(String(64), index=True)
    external_id: Mapped[str] = mapped_column(String(256))

    kind: Mapped[str] = mapped_column(String(64), index=True)  # email|meeting|invoice|ticket|...
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(256), default="")  # who it came from
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    raw: Mapped[dict] = mapped_column(JSON, default=dict)

    # --- priority engine output ---
    score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    factors: Mapped[dict] = mapped_column(JSON, default=dict)  # per-dimension breakdown
    reasons: Mapped[list] = mapped_column(JSON, default=list)  # human-readable "why"
    disposition: Mapped[str] = mapped_column(
        String(32), default=Disposition.BRIEFING.value, index=True
    )
    state: Mapped[str] = mapped_column(String(32), default="new", index=True)
    surfaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --------------------------------------------------------------------------- #
# What it decided to do
# --------------------------------------------------------------------------- #
class Task(Base, TimestampMixin):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    signal_id: Mapped[str | None] = mapped_column(ForeignKey("signals.id"), nullable=True)

    title: Mapped[str] = mapped_column(String(500))
    intent: Mapped[str] = mapped_column(Text, default="")  # the original ask, verbatim
    origin: Mapped[str] = mapped_column(String(32), default="user")  # user|proactive|scheduled
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.QUEUED.value, index=True)
    lead_agent: Mapped[str] = mapped_column(String(64), default="planner")

    plan: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    artifacts: Mapped[list] = mapped_column(JSON, default=list)  # dashboards, docs, drafts
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    steps: Mapped[list["TaskStep"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="TaskStep.idx"
    )


class TaskStep(Base, TimestampMixin):
    """One agent turn inside a task. This is the trace the Activity view renders."""

    __tablename__ = "task_steps"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer, default=0)
    agent: Mapped[str] = mapped_column(String(64))
    tool: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summary: Mapped[str] = mapped_column(String(500), default="")
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task: Mapped[Task] = relationship(back_populates="steps")


# --------------------------------------------------------------------------- #
# What it must ask about
# --------------------------------------------------------------------------- #
class Approval(Base, TimestampMixin):
    """A held action. Nothing in `payload` has happened yet."""

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True
    )
    step_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    action_type: Mapped[str] = mapped_column(String(64), index=True)  # send_email|payment|delete
    connector_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    risk_tier: Mapped[str] = mapped_column(String(32), default=RiskTier.REVIEW.value)
    title: Mapped[str] = mapped_column(String(500))
    rationale: Mapped[str] = mapped_column(Text, default="")  # why the assistant wants this
    preview: Mapped[dict] = mapped_column(JSON, default=dict)  # exactly what will happen
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # the call to execute on approve

    status: Mapped[str] = mapped_column(
        String(32), default=ApprovalStatus.PENDING.value, index=True
    )
    reversible: Mapped[bool] = mapped_column(Boolean, default=True)
    revert_plan: Mapped[dict] = mapped_column(JSON, default=dict)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_result: Mapped[dict] = mapped_column(JSON, default=dict)


# --------------------------------------------------------------------------- #
# What it remembers
# --------------------------------------------------------------------------- #
class Memory(Base, TimestampMixin):
    __tablename__ = "memories"
    __table_args__ = (Index("ix_memory_lookup", "user_id", "kind", "salience"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default=MemoryKind.LONG.value, index=True)
    key: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(200), default="")
    salience: Mapped[float] = mapped_column(Float, default=0.5)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)  # Qdrant in prod
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --------------------------------------------------------------------------- #
# The thread
# --------------------------------------------------------------------------- #
class Message(Base, TimestampMixin):
    """One ongoing thread per user - a quick reply and a weeklong project live here."""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32))  # user|assistant|system
    kind: Mapped[str] = mapped_column(String(32), default="text")  # text|brief|approval|task_update
    content: Mapped[str] = mapped_column(Text, default="")
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    approval_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    signal_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    proactive: Mapped[bool] = mapped_column(Boolean, default=False)


class Briefing(Base, TimestampMixin):
    __tablename__ = "briefings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    for_date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    headline: Mapped[str] = mapped_column(String(500), default="")
    sections: Mapped[list] = mapped_column(JSON, default=list)
    signal_ids: Mapped[list] = mapped_column(JSON, default=list)
    model: Mapped[str] = mapped_column(String(128), default="")
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --------------------------------------------------------------------------- #
# Governance
# --------------------------------------------------------------------------- #
class AuditLog(Base):
    """Append-only, hash-chained. Any gap or edit breaks verification."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(100), default="system")  # system|agent:x|user
    action: Mapped[str] = mapped_column(String(100), index=True)
    resource_type: Mapped[str] = mapped_column(String(64), default="")
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), default="ok")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    hash: Mapped[str] = mapped_column(String(64), default="")


class PolicyRule(Base, TimestampMixin):
    """User-tunable overrides for the priority engine and the approval gate."""

    __tablename__ = "policy_rules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # vip|mute|boost|auto_approve|never
    pattern: Mapped[str] = mapped_column(String(300))  # email, domain, connector, keyword
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    note: Mapped[str] = mapped_column(String(300), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class WorkflowRun(Base, TimestampMixin):
    """Durable state for the workflow engine. Survives a restart mid-flight."""

    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    workflow_key: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(32), default="scheduled", index=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
