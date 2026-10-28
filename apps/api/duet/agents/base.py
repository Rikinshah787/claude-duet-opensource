"""Agent base types.

Agents are narrow and stateless. They receive a context, do one kind of work,
and return a result. They never call a third-party API directly and they never
decide that an action is safe - those are the executor's and the Security
agent's jobs respectively.

The important constraint: an agent that wants to change the world outside this
process can only `propose()`. A proposal becomes an `Approval` row; nothing
happens until a human decides. There is no agent-side bypass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from ..models import RiskTier, User

if TYPE_CHECKING:
    from ..llm.client import LLMClient


@dataclass
class Proposal:
    """A world-changing action an agent wants to take."""

    action_type: str
    title: str
    rationale: str
    connector_key: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    preview: dict[str, Any] = field(default_factory=dict)
    risk: RiskTier | None = None  # Security agent fills this in if unset
    reversible: bool = True


@dataclass
class AgentResult:
    ok: bool
    summary: str
    output: dict[str, Any] = field(default_factory=dict)
    proposals: list[Proposal] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    memories: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class AgentContext:
    db: Session
    user: User
    llm: "LLMClient"
    task_id: str | None = None
    intent: str = ""
    step_input: dict[str, Any] = field(default_factory=dict)
    # Context assembled by the Memory agent before the step runs.
    recall: list[str] = field(default_factory=list)
    prior_outputs: dict[str, Any] = field(default_factory=dict)


class Agent:
    name: str = "agent"
    role: str = ""
    capabilities: tuple[str, ...] = ()

    def run(self, ctx: AgentContext) -> AgentResult:  # pragma: no cover - interface
        raise NotImplementedError

    # Convenience for building a prompt with recalled context attached.
    def _with_recall(self, ctx: AgentContext, prompt: str) -> str:
        if not ctx.recall:
            return prompt
        known = "\n".join(f"- {r}" for r in ctx.recall[:8])
        return f"What you already know about this user:\n{known}\n\n{prompt}"

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "capabilities": list(self.capabilities),
        }
