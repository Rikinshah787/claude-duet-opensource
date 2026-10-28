"""Connector protocol.

Every app the assistant touches goes through this interface, which splits reads
from writes on purpose:

  * `fetch()` is the proactive sweep. It is read-only and may run unattended.
  * `execute()` is the write path. It is only ever called by the approval
    executor, never directly by an agent, so there is exactly one chokepoint
    where a human gate can be enforced.

Adapters receive a `CredentialHandle`, not a credential. The handle resolves
inside the sandbox at call time; the model's context never contains the secret.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass
class RawSignal:
    """An observation, before the priority engine scores it."""

    external_id: str
    kind: str
    title: str
    body: str = ""
    actor: str = ""
    url: str | None = None
    occurred_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    # Adapter-supplied hints. The priority engine treats these as evidence, not
    # as a verdict - a connector cannot promote itself straight to a notification.
    hints: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionResult:
    ok: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    external_id: str | None = None
    revert_plan: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class CredentialHandle:
    """Opaque pointer to a secret held in the vault."""

    ref: str
    connector_key: str

    def __repr__(self) -> str:  # never let a secret-ish value reach a log
        return f"<CredentialHandle {self.connector_key}:{self.ref[:6]}...>"


@dataclass
class FetchContext:
    user_id: str
    connection_id: str
    since: datetime | None
    cursor: dict[str, Any]
    credential: CredentialHandle | None = None


@runtime_checkable
class Connector(Protocol):
    key: str

    def fetch(self, ctx: FetchContext) -> tuple[list[RawSignal], dict[str, Any]]:
        """Return new observations and the updated cursor. Read-only."""
        ...

    def execute(self, action: str, payload: dict[str, Any], ctx: FetchContext) -> ActionResult:
        """Perform one approved write. Called only by the approval executor."""
        ...


class BaseConnector:
    """Default behaviour: readable, with every write rejected until implemented."""

    key: str = "base"

    def fetch(self, ctx: FetchContext) -> tuple[list[RawSignal], dict[str, Any]]:
        return [], ctx.cursor

    def execute(self, action: str, payload: dict[str, Any], ctx: FetchContext) -> ActionResult:
        return ActionResult(
            ok=False,
            summary=f"{self.key}.{action} is not implemented",
            error="not_implemented",
        )
