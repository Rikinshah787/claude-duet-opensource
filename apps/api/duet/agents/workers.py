"""Agents 2-6: Research, Communication, Productivity, Analytics, Automation.

These are the specialists the planner dispatches to. Each one reads real rows
from the signal store, so their output reflects what was actually observed
rather than what a model guessed. Where a model is configured it improves the
prose; where it is not, the facts are identical.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from ..models import Memory, RiskTier, Signal
from .base import Agent, AgentContext, AgentResult, Proposal

STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "for", "of", "in", "on", "at", "is", "are",
    "was", "were", "be", "with", "my", "me", "i", "you", "your", "it", "this", "that",
    "please", "can", "could", "would", "should", "about", "from", "get", "got", "need",
    "want", "draft", "write", "send", "make", "create", "what", "when", "who", "how",
}


def _keywords(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9][a-z0-9'-]{2,}", (text or "").lower())
    return [w for w in words if w not in STOPWORDS][:14]


def _is_hostile(sig: Signal) -> bool:
    """True when ingest flagged this observation as a hostile injection attempt."""
    return bool(((sig.raw or {}).get("hints", {}).get("injection") or {}).get("hostile"))


def _recent_signals(ctx: AgentContext, limit: int = 220) -> list[Signal]:
    """Recent observations, with hostile ones removed.

    Withholding the payload from prompts is not enough on its own. A phishing
    message still carries a plausible subject line and a sender address, and an
    agent that treats it as ordinary context will happily surface it as a
    finding and then address a reply to the attacker. Content *and* metadata
    from a hostile message have to leave the working set together.
    """
    rows = list(
        ctx.db.execute(
            select(Signal)
            .where(Signal.user_id == ctx.user.id)
            .order_by(Signal.occurred_at.desc())
            .limit(limit)
        ).scalars()
    )
    return [s for s in rows if not _is_hostile(s)]


def _rank(signals: list[Signal], terms: list[str]) -> list[tuple[Signal, float]]:
    """Keyword overlap plus the signal's own priority score.

    A keyword index is enough at laptop scale. In deployment this is the call
    that goes to the vector store; the ranking contract stays the same.
    """
    scored: list[tuple[Signal, float]] = []
    for sig in signals:
        haystack = f"{sig.title} {sig.body} {sig.actor} {sig.connector_key} {sig.kind}".lower()
        hits = sum(1 for t in terms if t in haystack)
        if not hits and terms:
            continue
        scored.append((sig, hits * 10 + sig.score * 0.25))
    scored.sort(key=lambda p: p[1], reverse=True)
    return scored


# --------------------------------------------------------------------------- #
class ResearchAgent(Agent):
    name = "research"
    role = "Research"
    capabilities = ("signal search", "document retrieval", "account history")

    def run(self, ctx: AgentContext) -> AgentResult:
        query = ctx.step_input.get("instruction") or ctx.intent
        terms = _keywords(query)
        signals = _recent_signals(ctx)
        ranked = _rank(signals, terms)[:8]

        if not ranked:  # nothing matched - fall back to what is most important now
            ranked = [(s, s.score) for s in sorted(signals, key=lambda s: s.score, reverse=True)[:6]]

        findings = [
            {
                "id": s.id,
                "connector": s.connector_key,
                "kind": s.kind,
                "title": s.title,
                "actor": s.actor,
                "body": s.body[:400],
                "score": round(s.score, 1),
                "when": s.occurred_at.isoformat(),
                # Hostile signals never reach here (filtered in _recent_signals),
                # but a merely *suspicious* one still must not be replied to.
                "flagged": bool(
                    (s.raw or {}).get("hints", {}).get("injection")
                ),
            }
            for s, _ in ranked
        ]

        mems = list(
            ctx.db.execute(
                select(Memory)
                .where(Memory.user_id == ctx.user.id)
                .order_by(Memory.salience.desc())
                .limit(6)
            ).scalars()
        )

        lines = [f"- [{f['connector']}] {f['title']}" for f in findings[:6]]
        summary = (
            f"Found {len(findings)} relevant item(s)."
            if findings
            else "No matching activity in the connected apps."
        )

        return AgentResult(
            ok=True,
            summary=summary,
            output={
                "findings": findings,
                "context": [m.content for m in mems],
                "digest": "\n".join(lines),
                "query": query,
            },
        )


# --------------------------------------------------------------------------- #
DRAFT_SYSTEM = """You write on behalf of a busy operator. Rules:
- Plain, direct, warm-but-brief. No filler openers, no "I hope this finds you well".
- Reference the specific facts given. Never invent numbers, dates or commitments.
- If a fact is missing, leave a [bracketed placeholder] rather than guessing.
- 120 words or fewer unless the context clearly needs more.
Return JSON: {"subject": str, "body": str}"""


class CommunicationAgent(Agent):
    name = "communication"
    role = "Communication"
    capabilities = ("email drafting", "messaging", "follow-ups")

    def run(self, ctx: AgentContext) -> AgentResult:
        instruction = ctx.step_input.get("instruction") or ctx.intent
        research = ctx.prior_outputs.get("research", {})
        findings = research.get("findings", [])

        target = self._pick_target(instruction, findings)
        channel, connector = self._channel_for(target, instruction)

        facts = self._facts(findings)
        draft = self._draft(ctx, instruction, target, facts)

        preview = {
            "channel": channel,
            "to": target.get("actor") or "[recipient]",
            "subject": draft["subject"],
            "body": draft["body"],
            "in_reply_to": target.get("title"),
        }
        action = "post_message" if channel == "slack" else "send_email"

        proposal = Proposal(
            action_type=action,
            connector_key=connector,
            title=f"Send to {preview['to']}: {draft['subject']}",
            rationale=(
                f"You asked to {instruction.lower()[:120]}. "
                f"Drafted from {len(findings)} item(s) in your connected apps."
            ),
            payload={
                "to": preview["to"],
                "subject": draft["subject"],
                "body": draft["body"],
                "channel": preview.get("to"),
            },
            preview=preview,
            risk=RiskTier.REVIEW,
            reversible=False,
        )

        return AgentResult(
            ok=True,
            summary=f"Drafted a {channel} message to {preview['to']} (held for your approval)",
            output={"draft": draft, "target": target, "channel": channel},
            proposals=[proposal],
            artifacts=[{"type": "draft", "title": draft["subject"], "body": draft["body"]}],
        )

    # -------------------------------------------------------------- #
    def _pick_target(self, instruction: str, findings: list[dict]) -> dict:
        """Pick who this is actually addressed to.

        Guards against the obvious failure: an invoice reminder has your own
        billing robot as its sender, and replying to that sends a dunning letter
        to yourself. Automated and internal senders are therefore never chosen
        as a recipient - if nothing better exists the draft keeps a placeholder,
        which the approval screen shows you before anything goes out.
        """
        candidates = [f for f in findings if not f.get("flagged")]

        named = re.findall(r"\b([A-Z][a-z]{2,})\b", instruction)
        for name in named:
            for f in candidates:
                if name.lower() in (f.get("actor", "") + f.get("title", "")).lower():
                    if self._addressable(f.get("actor", "")):
                        return f

        for f in candidates:
            if self._addressable(f.get("actor", "")):
                return f

        # Nothing addressable: keep the richest unflagged finding for context,
        # drop the sender so the draft renders "[recipient]" rather than a wrong
        # address. Never fall back to a flagged one - that is how a phishing
        # sender becomes the recipient.
        if candidates:
            return {**candidates[0], "actor": ""}
        return {}

    # Senders that are robots or our own systems, never a counterparty.
    NO_REPLY = re.compile(
        r"^(billing|no-?reply|noreply|notifications?|receipts?|news|newsletter|"
        r"support|do-?not-?reply|mailer|postmaster|automated|alerts?)@",
        re.I,
    )

    def _addressable(self, actor: str) -> bool:
        actor = (actor or "").strip()
        if not actor or "@" not in actor:
            return False
        if self.NO_REPLY.match(actor):
            return False
        # Our own domain is internal, not a customer to chase.
        return not actor.lower().endswith("@yourcompany.com")

    def _channel_for(self, target: dict, instruction: str) -> tuple[str, str]:
        if re.search(r"\bslack\b", instruction, re.I):
            return "slack", "slack"
        connector = target.get("connector", "gmail")
        if connector in {"slack", "teams", "discord"}:
            return "slack" if connector == "slack" else connector, connector
        return "email", connector if connector in {"gmail", "outlook"} else "gmail"

    def _facts(self, findings: list[dict]) -> str:
        """Render findings for a prompt.

        Everything here was written by a third party inside someone else's
        email or ticket, so it is framed as untrusted data rather than pasted
        in raw. `wrap_untrusted` also withholds anything that scored as hostile,
        so a payload that survived ingest still never reaches the model.
        """
        from ..core.injection import wrap_untrusted

        body = "\n".join(
            f"- [{f['connector']}/{f['kind']}] {f['title']}"
            + (f" :: {f['body'][:200]}" if f.get("body") else "")
            for f in findings[:6]
        )
        if not body:
            return ""
        sources = ", ".join(sorted({f["connector"] for f in findings[:6]}))
        return wrap_untrusted(body, source=sources or "connected apps")

    def _draft(self, ctx: AgentContext, instruction: str, target: dict, facts: str) -> dict:
        subject = target.get("title") or "Following up"
        if not subject.lower().startswith("re:") and target:
            subject = f"Re: {subject}"

        fallback_body = self._template_body(instruction, target, facts)

        if ctx.llm.provider != "heuristic":
            result = ctx.llm.complete_json(
                self._with_recall(
                    ctx,
                    f"Task: {instruction}\n\n"
                    f"Recipient: {target.get('actor', 'unknown')}\n"
                    f"Thread: {target.get('title', 'n/a')}\n\n"
                    f"Facts you may use:\n{facts or '(none)'}",
                ),
                system=DRAFT_SYSTEM,
                fallback=None,
            )
            if isinstance(result, dict) and result.get("body"):
                return {
                    "subject": str(result.get("subject") or subject)[:200],
                    "body": str(result["body"])[:4000],
                }

        return {"subject": subject[:200], "body": fallback_body}

    def _template_body(self, instruction: str, target: dict, facts: str) -> str:
        """Deterministic draft. Honest about being a starting point."""
        kind = (target.get("kind") or "").lower()
        who = (target.get("actor") or "").split("@")[0].split(".")[0].title() or "there"

        if "invoice" in kind or "overdue" in instruction.lower():
            return (
                f"Hi {who},\n\n"
                f"Following up on {target.get('title', 'the outstanding invoice')}. "
                "It's past terms now and I wanted to check whether it's stuck somewhere "
                "on your side.\n\n"
                "If there's a PO or approval step I can help unblock, let me know and "
                "I'll get it sorted today.\n\n"
                "Thanks,\n[your name]"
            )
        if kind in {"escalation", "ticket"} or "escalation" in instruction.lower():
            return (
                f"Hi {who},\n\n"
                "You're right to push on this, and I'm sorry it's taken three rounds.\n\n"
                "Here's where it actually stands: [current status]. The fix is "
                "[what changes], and I'll confirm back by [date].\n\n"
                "I'd rather over-communicate than have you chasing us again.\n\n"
                "[your name]"
            )
        if "meeting" in kind or "reschedul" in instruction.lower():
            return (
                f"Hi {who},\n\n"
                "Quick scheduling note on "
                f"{target.get('title', 'our upcoming meeting')} - "
                "[proposed change].\n\n"
                "Does that work on your end?\n\n"
                "[your name]"
            )

        head = f"Hi {who},\n\n"
        ref = (
            f"Picking up on {target.get('title')}.\n\n"
            if target.get("title")
            else ""
        )
        return (
            head
            + ref
            + "[the point you want to make]\n\n"
            + "Let me know and I'll take it from there.\n\n[your name]"
        )


# --------------------------------------------------------------------------- #
class ProductivityAgent(Agent):
    name = "productivity"
    role = "Productivity"
    capabilities = ("calendar management", "scheduling", "conflict resolution")

    def run(self, ctx: AgentContext) -> AgentResult:
        instruction = ctx.step_input.get("instruction") or ctx.intent
        signals = _recent_signals(ctx)
        cal = [s for s in signals if s.connector_key in {"google_calendar", "outlook_calendar", "zoom", "calendly"}]
        conflicts = [s for s in cal if s.kind in {"conflict", "prep_needed"}]
        upcoming = sorted(
            [s for s in cal if s.occurred_at > datetime.now(timezone.utc)],
            key=lambda s: s.occurred_at,
        )[:8]

        proposals: list[Proposal] = []
        for c in conflicts[:3]:
            proposals.append(
                Proposal(
                    action_type="move_event",
                    connector_key="google_calendar",
                    title=f"Resolve: {c.title}",
                    rationale=(c.body or "Two commitments overlap.")
                    + " Moving the lower-priority one is the smaller disruption.",
                    payload={
                        "event_id": c.external_id,
                        "new_time": "[proposed slot]",
                        "notify_attendees": True,
                    },
                    preview={
                        "event": c.title,
                        "change": "Move to the next free slot and notify attendees",
                        "detail": c.body,
                    },
                    risk=RiskTier.REVIEW,
                )
            )

        schedule = [
            {
                "title": s.title,
                "when": s.occurred_at.isoformat(),
                "kind": s.kind,
                "detail": s.body[:200],
            }
            for s in upcoming
        ]

        if conflicts:
            summary = f"{len(conflicts)} scheduling problem(s) found; fixes proposed."
        elif upcoming:
            summary = f"Calendar is clean. {len(upcoming)} item(s) coming up."
        else:
            summary = "Nothing on the calendar in range."

        return AgentResult(
            ok=True,
            summary=summary,
            output={
                "conflicts": [{"title": c.title, "detail": c.body} for c in conflicts],
                "upcoming": schedule,
            },
            proposals=proposals,
        )


# --------------------------------------------------------------------------- #
class AnalyticsAgent(Agent):
    name = "analytics"
    role = "Analytics"
    capabilities = ("dashboards", "forecasting", "reporting")

    def run(self, ctx: AgentContext) -> AgentResult:
        signals = _recent_signals(ctx)
        finance = self._finance(signals)
        forecast = self._runway(finance, ctx.user.finance or {})
        by_connector = defaultdict(int)
        by_kind = defaultdict(int)
        for s in signals:
            by_connector[s.connector_key] += 1
            by_kind[s.kind] += 1

        dashboard = {
            "type": "dashboard",
            "title": "Operating snapshot",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tiles": [
                {
                    "label": "Overdue receivables",
                    "value": f"${finance['overdue_total']:,.0f}",
                    "detail": f"{finance['overdue_count']} invoice(s), oldest "
                              f"{finance['oldest_days']} days",
                    "tone": "warn" if finance["overdue_total"] > 0 else "ok",
                },
                {
                    "label": "Revenue at risk",
                    "value": f"${finance['at_risk']:,.0f}",
                    "detail": "Failed payments, disputes and churn-flagged accounts",
                    "tone": "bad" if finance["at_risk"] > 50_000 else "warn",
                },
                {
                    "label": "Open escalations",
                    "value": str(finance["escalations"]),
                    "detail": "Customer issues raised in the last 48h",
                    "tone": "bad" if finance["escalations"] >= 2 else "ok",
                },
                {
                    "label": "Items needing you",
                    "value": str(finance["needs_you"]),
                    "detail": "Scored above the attention threshold",
                    "tone": "warn",
                },
            ],
            "series": [
                {
                    "label": "Signals by source",
                    "kind": "bar",
                    "data": sorted(
                        [{"name": k, "value": v} for k, v in by_connector.items()],
                        key=lambda d: d["value"],
                        reverse=True,
                    )[:8],
                },
                {
                    "label": "Cash runway projection",
                    "kind": "line",
                    "data": forecast["series"],
                    "note": forecast["note"],
                },
            ],
        }

        return AgentResult(
            ok=True,
            summary=(
                f"Built a snapshot: ${finance['overdue_total']:,.0f} overdue, "
                f"${finance['at_risk']:,.0f} at risk, {finance['escalations']} escalation(s)."
            ),
            output={"finance": finance, "forecast": forecast},
            artifacts=[dashboard],
        )

    # -------------------------------------------------------------- #
    def _finance(self, signals: list[Signal]) -> dict[str, Any]:
        overdue_total = 0.0
        overdue_count = 0
        oldest = 0
        at_risk = 0.0
        escalations = 0
        needs_you = 0

        for s in signals:
            hints = (s.raw or {}).get("hints", {})
            amount = float(hints.get("amount") or 0)

            if s.kind == "invoice_overdue":
                overdue_total += amount
                overdue_count += 1
                oldest = max(oldest, int(hints.get("days_overdue") or 0))
            if s.kind in {"payment_failed", "dispute", "deal_risk", "revenue_risk"}:
                at_risk += amount
            if s.kind in {"escalation", "sla_breach"}:
                escalations += 1
            if s.score >= 60:
                needs_you += 1

        return {
            "overdue_total": overdue_total,
            "overdue_count": overdue_count,
            "oldest_days": oldest,
            "at_risk": at_risk,
            "escalations": escalations,
            "needs_you": needs_you,
        }

    def _runway(self, finance: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        """Project cash, but only from figures the user actually gave us.

        An invented starting balance renders identically to a real one, and an
        operator would reasonably act on it. So with no configured cash position
        this returns no projection and says what is missing, rather than
        producing a confident line through made-up numbers. The receivables
        figure below is real - it is summed from observed invoices.
        """
        starting_cash = profile.get("starting_cash")
        monthly_burn = profile.get("monthly_burn")
        collection_rate = float(profile.get("ar_collection_rate", 0.6))
        collected = finance["overdue_total"] * collection_rate

        if not starting_cash or not monthly_burn:
            return {
                "configured": False,
                "series": [],
                "weeks_remaining": None,
                "assumptions": {"ar_collection_rate": collection_rate},
                "collectible_ar": round(collected),
                "note": (
                    "No cash projection: set your starting cash and monthly burn "
                    "in Settings. I won't guess at a runway figure. "
                    f"From observed invoices, ${collected:,.0f} of receivables "
                    "looks collectible."
                ),
            }

        starting_cash = float(starting_cash)
        monthly_burn = float(monthly_burn)

        series = []
        cash = starting_cash
        today = datetime.now(timezone.utc)
        for week in range(0, 17, 2):
            point_cash = cash - (monthly_burn / 4.345) * week
            if week >= 4:
                point_cash += collected
            series.append(
                {
                    "name": (today + timedelta(weeks=week)).strftime("%b %d"),
                    "value": round(point_cash),
                }
            )

        weeks_left = (starting_cash + collected) / (monthly_burn / 4.345)
        return {
            "configured": True,
            "series": series,
            "weeks_remaining": round(weeks_left, 1),
            "collectible_ar": round(collected),
            "assumptions": {
                "starting_cash": starting_cash,
                "monthly_burn": monthly_burn,
                "ar_collection_rate": collection_rate,
            },
            "note": (
                f"Your figures: ${starting_cash:,.0f} cash, ${monthly_burn:,.0f}/mo burn, "
                f"{collection_rate:.0%} of overdue AR collected. Runway ~{weeks_left:.0f} weeks."
            ),
        }


# --------------------------------------------------------------------------- #
class AutomationAgent(Agent):
    name = "automation"
    role = "Automation"
    capabilities = ("workflow execution", "record creation", "app integration")

    # Which connector handles which verb, when the instruction does not say.
    DEFAULTS = {
        "issue": ("linear", "create_issue"),
        "ticket": ("jira", "create_issue"),
        "task": ("asana", "create_task"),
        "page": ("notion", "create_page"),
        "record": ("hubspot", "update_deal"),
    }

    def run(self, ctx: AgentContext) -> AgentResult:
        instruction = ctx.step_input.get("instruction") or ctx.intent
        research = ctx.prior_outputs.get("research", {})
        findings = research.get("findings", [])

        connector, action = self._route(instruction)
        title = self._title(instruction, findings)
        body = self._body(instruction, findings)

        proposal = Proposal(
            action_type=action,
            connector_key=connector,
            title=f"{action.replace('_', ' ').title()} in {connector}: {title}",
            rationale=f"Part of: {instruction[:160]}",
            payload={"title": title, "description": body, "connector": connector},
            preview={"app": connector, "action": action, "title": title, "description": body},
            risk=RiskTier.SAFE if action.startswith("create") else RiskTier.REVIEW,
        )

        return AgentResult(
            ok=True,
            summary=f"Prepared {action.replace('_', ' ')} in {connector}",
            output={"connector": connector, "action": action, "title": title},
            proposals=[proposal],
        )

    def _route(self, instruction: str) -> tuple[str, str]:
        text = instruction.lower()
        for app in ("jira", "linear", "asana", "notion", "clickup", "trello", "hubspot", "monday"):
            if app in text:
                verb = "create_task" if app in {"asana", "clickup"} else (
                    "create_page" if app == "notion" else (
                        "update_deal" if app == "hubspot" else "create_issue"
                    )
                )
                return app, verb
        for noun, (app, verb) in self.DEFAULTS.items():
            if noun in text:
                return app, verb
        return "linear", "create_issue"

    def _title(self, instruction: str, findings: list[dict]) -> str:
        cleaned = re.sub(
            r"^(please\s+)?(create|open|file|make|add)\s+(a|an|the)?\s*"
            r"(ticket|issue|task|record|page)\s*(for|about|to)?\s*",
            "",
            instruction.strip(),
            flags=re.I,
        ).strip()
        if cleaned:
            return cleaned[:160]
        return (findings[0]["title"] if findings else instruction)[:160]

    def _body(self, instruction: str, findings: list[dict]) -> str:
        lines = [f"Created by the assistant from: {instruction}", ""]
        if findings:
            lines.append("Related activity:")
            lines += [f"- [{f['connector']}] {f['title']}" for f in findings[:5]]
        return "\n".join(lines)
