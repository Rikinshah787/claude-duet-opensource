"""Agent 7 - Security.

Every proposal passes through here before it can become an approval request.
Four checks, in order of severity:

1. **Policy floor** - some actions are refused outright. Moving money between
   accounts and trading securities are not approvable; a user saying yes does
   not unlock them.
2. **Scope** - is the app actually connected, enabled, and granted the write
   scope this action needs? An agent asking to post to Slack when Slack was
   disconnected should fail closed.
3. **Egress** - does the content carry something that must not leave? Run
   against the rendered message, not the instruction, because that is what
   actually gets sent.
4. **Risk tier** - set from the connector catalog, which is the single place
   risk is declared. An unrecognised verb is treated as sensitive, never safe.

The agent can downgrade nothing. It can only hold, block, or let an action
proceed to the human gate.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ..connectors import catalog
from ..models import Connection, PolicyRule, RiskTier
from .base import Agent, AgentContext, AgentResult, Proposal

# Refused regardless of consent. Mirrors the product's stated hard limits.
FORBIDDEN_ACTIONS: dict[str, str] = {
    "transfer_balance": "Moving money between accounts is not something I can do.",
    "send_payment": "Sending payments directly is not something I can do.",
    "trade_securities": "Trading securities is not something I can do.",
    "change_mfa": "Changing multi-factor settings has to be done by you.",
    "rotate_root_credentials": "Rotating root credentials has to be done by you.",
}


class SecurityVerdict:
    __slots__ = ("proposal", "allowed", "risk", "reasons", "blocked_reason")

    def __init__(
        self,
        proposal: Proposal,
        allowed: bool,
        risk: RiskTier,
        reasons: list[str],
        blocked_reason: str | None = None,
    ):
        self.proposal = proposal
        self.allowed = allowed
        self.risk = risk
        self.reasons = reasons
        self.blocked_reason = blocked_reason

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.proposal.action_type,
            "connector": self.proposal.connector_key,
            "allowed": self.allowed,
            "risk": self.risk.value,
            "reasons": self.reasons,
            "blocked_reason": self.blocked_reason,
        }


class SecurityAgent(Agent):
    name = "security"
    role = "Security"
    capabilities = ("permission verification", "risk analysis", "egress control")

    def run(self, ctx: AgentContext) -> AgentResult:
        proposals: list[Proposal] = ctx.step_input.get("proposals", [])
        user_directed: bool = bool(ctx.step_input.get("user_directed", False))

        verdicts = [self.review(ctx, p, user_directed=user_directed) for p in proposals]
        allowed = [v for v in verdicts if v.allowed]
        blocked = [v for v in verdicts if not v.allowed]

        if blocked:
            summary = (
                f"{len(allowed)} action(s) cleared for your approval, "
                f"{len(blocked)} blocked."
            )
        else:
            summary = f"{len(allowed)} action(s) cleared for your approval."

        return AgentResult(
            ok=True,
            summary=summary,
            output={
                "verdicts": [v.as_dict() for v in verdicts],
                "blocked": [v.blocked_reason for v in blocked if v.blocked_reason],
            },
            proposals=[v.proposal for v in allowed],
        )

    # ------------------------------------------------------------------ #
    def review(
        self, ctx: AgentContext, proposal: Proposal, *, user_directed: bool = False
    ) -> SecurityVerdict:
        reasons: list[str] = []

        # 1. Policy floor
        if proposal.action_type in FORBIDDEN_ACTIONS:
            return SecurityVerdict(
                proposal,
                allowed=False,
                risk=RiskTier.FORBIDDEN,
                reasons=["action is on the policy deny list"],
                blocked_reason=FORBIDDEN_ACTIONS[proposal.action_type],
            )

        risk = proposal.risk or RiskTier.SENSITIVE
        if proposal.connector_key:
            declared = catalog.risk_for(proposal.connector_key, proposal.action_type)
            # Catalog wins over whatever the proposing agent claimed.
            risk = declared
            if declared is RiskTier.FORBIDDEN:
                return SecurityVerdict(
                    proposal,
                    allowed=False,
                    risk=risk,
                    reasons=["connector declares this action forbidden"],
                    blocked_reason=(
                        f"{proposal.connector_key}.{proposal.action_type} is blocked by policy."
                    ),
                )

        # 2. Scope
        if proposal.connector_key:
            ok, why = self._scope_ok(ctx, proposal)
            if not ok:
                return SecurityVerdict(
                    proposal,
                    allowed=False,
                    risk=risk,
                    reasons=["scope check failed"],
                    blocked_reason=why,
                )
            reasons.append("connected app has the required scope")

        # 3. Egress
        content = self._renderable(proposal)
        if content:
            from ..core.redaction import check_egress

            destination = (
                proposal.payload.get("to")
                or proposal.payload.get("channel")
                or proposal.connector_key
                or "an external service"
            )
            decision = check_egress(content, destination=str(destination), user_directed=user_directed)
            if not decision.allowed:
                return SecurityVerdict(
                    proposal,
                    allowed=False,
                    risk=risk,
                    reasons=["content failed the egress check"],
                    blocked_reason=decision.reason,
                )
            reasons.append("no sensitive content in the payload")

        # 4. Risk tier and user rules
        rules = list(
            ctx.db.execute(
                select(PolicyRule).where(
                    PolicyRule.user_id == ctx.user.id, PolicyRule.enabled.is_(True)
                )
            ).scalars()
        )
        for rule in rules:
            if rule.kind == "never" and rule.pattern.lower() in proposal.action_type.lower():
                return SecurityVerdict(
                    proposal,
                    allowed=False,
                    risk=risk,
                    reasons=["user rule forbids this action"],
                    blocked_reason=f"You asked me never to {rule.pattern}.",
                )

        reasons.append(f"risk tier: {risk.value}")
        if not proposal.reversible:
            reasons.append("not reversible once done")

        proposal.risk = risk
        return SecurityVerdict(proposal, allowed=True, risk=risk, reasons=reasons)

    # ------------------------------------------------------------------ #
    def _scope_ok(self, ctx: AgentContext, proposal: Proposal) -> tuple[bool, str]:
        conn = ctx.db.execute(
            select(Connection).where(
                Connection.user_id == ctx.user.id,
                Connection.connector_key == proposal.connector_key,
            )
        ).scalar_one_or_none()

        if conn is None:
            return False, f"{proposal.connector_key} is not connected."
        if not conn.enabled or conn.status != "connected":
            return False, f"{proposal.connector_key} is disconnected or disabled."

        spec = catalog.get(proposal.connector_key)
        if spec and spec.write_scopes:
            granted = set(conn.scopes or [])
            if granted and not granted.intersection(set(spec.write_scopes)):
                return False, (
                    f"{proposal.connector_key} is connected read-only - "
                    "it has no write scope granted."
                )
        return True, ""

    def _renderable(self, proposal: Proposal) -> str:
        """The text that would actually leave the machine."""
        parts: list[str] = []
        for source in (proposal.preview, proposal.payload):
            for key in ("subject", "body", "message", "description", "text", "content"):
                value = source.get(key)
                if isinstance(value, str):
                    parts.append(value)
        return "\n".join(dict.fromkeys(parts))
