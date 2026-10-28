"""Agent 1 - Executive Planner.

Turns a request into an ordered plan of steps, each assigned to a specialist.

Planning runs through the model when one is available and falls back to keyword
routing when it is not. The fallback is not a degraded mode for the common
cases: "draft a reply to Dana" has exactly one sensible plan, and spending a
model turn to rediscover that wastes the user's plan limits. The model earns its
place on the open-ended asks - "get me ready for the board meeting" - where the
decomposition is the hard part.
"""
from __future__ import annotations

import re
from typing import Any

from .base import Agent, AgentContext, AgentResult

PLANNER_SYSTEM = """You are the Executive Planner for a proactive assistant.
Break the user's request into the fewest steps that fully accomplish it.

Available specialists:
- research: search connected apps, documents and the signal history
- communication: draft emails, messages and follow-ups (drafts only - sending is gated)
- productivity: calendars, scheduling, conflicts, task tracking
- analytics: dashboards, forecasts, reports, spreadsheet analysis
- automation: create or update records in connected apps (tickets, CRM, docs)

Return JSON: {"title": str, "steps": [{"agent": str, "instruction": str}]}
Rules: 1-5 steps. Research before writing. Never include an "approve" or "send"
step - sending is handled outside the plan. An instruction describes work for
one of the specialists above and nothing else: never refer to your own tools,
environment or session, and never name another product's settings as the place
to fix something."""


# Routing table. Order matters: the first match that fires wins the lead agent.
ROUTES: tuple[tuple[str, re.Pattern], ...] = (
    (
        "analytics",
        re.compile(
            r"\b(dashboard|forecast|report|analy[sz]e|analytics|metrics?|revenue|runway|"
            r"burn|chart|spreadsheet|model|projection|pipeline report|kpi)\b",
            re.I,
        ),
    ),
    (
        "productivity",
        re.compile(
            r"\b(schedul\w*|calendar|meeting|reschedul\w*|move (?:the|my) \w+|book|"
            r"conflict|availability|free time|agenda|invite)\b",
            re.I,
        ),
    ),
    (
        "communication",
        re.compile(
            r"\b(draft|reply|respond|email|message|follow[- ]?up|write to|send|"
            r"reach out|chase|remind(?:er)? to \w+)\b",
            re.I,
        ),
    ),
    (
        "automation",
        re.compile(
            r"\b(create (?:a )?(?:ticket|issue|task|record|page)|update (?:the )?(?:crm|deal|"
            r"project|ticket)|file (?:a )?(?:bug|ticket)|log (?:it|this|activity)|jira|linear|"
            r"notion page)\b",
            re.I,
        ),
    ),
    (
        "research",
        re.compile(
            r"\b(research|find|look up|search|summari[sz]e|brief me|what(?:'s| is) the status|"
            r"catch me up|background on|who is|compare)\b",
            re.I,
        ),
    ),
)

# Requests that genuinely need more than one specialist.
COMPOSITES: tuple[tuple[re.Pattern, list[tuple[str, str]]], ...] = (
    (
        re.compile(r"\b(prep|prepare|ready|brief).{0,24}\b(meeting|call|board|investor|qbr)\b", re.I),
        [
            ("research", "Gather everything relevant to this meeting: attendees, recent "
                         "threads, open items and any commitments made."),
            ("analytics", "Pull the numbers that will be asked about and note anything "
                          "that moved unexpectedly."),
            ("communication", "Write a one-page prep note: context, what they want, "
                              "what to say, and the open risks."),
        ],
    ),
    (
        re.compile(r"\b(chase|collect|follow up).{0,24}\b(invoice|payment|overdue|receivable)\b", re.I),
        [
            ("research", "Identify every overdue invoice, its age, amount and prior "
                         "contact history."),
            ("communication", "Draft a reminder for each one, escalating in tone with age."),
        ],
    ),
    (
        re.compile(r"\b(fix|resolve|handle|deal with).{0,24}\b(escalation|churn|angry|at risk)\b", re.I),
        [
            ("research", "Assemble the full history of this account: tickets, threads, "
                         "incidents and renewal position."),
            ("communication", "Draft a response that acknowledges specifics and commits "
                              "to something concrete."),
            ("automation", "Create a tracking issue so the fix has an owner."),
        ],
    ),
)


class PlannerAgent(Agent):
    name = "planner"
    role = "Executive Planner"
    capabilities = ("goal decomposition", "prioritisation", "action planning")

    def run(self, ctx: AgentContext) -> AgentResult:
        intent = (ctx.intent or "").strip()
        if not intent:
            return AgentResult(ok=False, summary="Nothing to plan", error="empty_intent")

        plan = self._heuristic_plan(intent)
        source = "heuristic"

        if ctx.llm.provider != "heuristic":
            drafted = ctx.llm.complete_json(
                self._with_recall(ctx, f"Request: {intent}"),
                system=PLANNER_SYSTEM,
                fallback=None,
            )
            validated = _validate(drafted)
            if validated:
                plan = validated
                source = ctx.llm.provider

        return AgentResult(
            ok=True,
            summary=f"Planned {len(plan['steps'])} step(s) for: {plan['title']}",
            output={"plan": plan["steps"], "title": plan["title"], "planner": source},
        )

    # ------------------------------------------------------------------ #
    def _heuristic_plan(self, intent: str) -> dict[str, Any]:
        for pattern, steps in COMPOSITES:
            if pattern.search(intent):
                return {
                    "title": _title_from(intent),
                    "steps": [{"agent": a, "instruction": i} for a, i in steps],
                }

        lead = "research"
        for agent_name, pattern in ROUTES:
            if pattern.search(intent):
                lead = agent_name
                break

        steps: list[dict[str, str]] = []
        # Anything that writes to a person needs grounding first.
        if lead in {"communication", "automation"}:
            steps.append(
                {
                    "agent": "research",
                    "instruction": f"Find the context needed to handle: {intent}",
                }
            )
        steps.append({"agent": lead, "instruction": intent})

        return {"title": _title_from(intent), "steps": steps}


def _validate(raw: Any) -> dict[str, Any] | None:
    """Never trust model-shaped output straight into the executor."""
    if not isinstance(raw, dict):
        return None
    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        return None

    allowed = {"research", "communication", "productivity", "analytics", "automation"}
    steps: list[dict[str, str]] = []
    for step in steps_raw[:5]:
        if not isinstance(step, dict):
            continue
        agent = str(step.get("agent", "")).strip().lower()
        instruction = str(step.get("instruction", "")).strip()
        if agent in allowed and instruction:
            steps.append({"agent": agent, "instruction": instruction[:600]})

    if not steps:
        return None
    title = str(raw.get("title") or "").strip()[:200]
    return {"title": title or "Task", "steps": steps}


def _title_from(intent: str) -> str:
    text = " ".join(intent.split())
    if len(text) <= 70:
        return text[:1].upper() + text[1:]
    cut = text[:70].rsplit(" ", 1)[0]
    return (cut[:1].upper() + cut[1:]) + "..."
