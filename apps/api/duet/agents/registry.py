"""Agent registry."""
from __future__ import annotations

from .base import Agent
from .memory import MemoryAgent
from .planner import PlannerAgent
from .security import SecurityAgent
from .workers import (
    AnalyticsAgent,
    AutomationAgent,
    CommunicationAgent,
    ProductivityAgent,
    ResearchAgent,
)

AGENTS: dict[str, Agent] = {
    "planner": PlannerAgent(),
    "research": ResearchAgent(),
    "communication": CommunicationAgent(),
    "productivity": ProductivityAgent(),
    "analytics": AnalyticsAgent(),
    "automation": AutomationAgent(),
    "security": SecurityAgent(),
    "memory": MemoryAgent(),
}

# The order the Agent Health dashboard lists them in.
ORDER = (
    "planner",
    "research",
    "communication",
    "productivity",
    "analytics",
    "automation",
    "security",
    "memory",
)


def get_agent(name: str) -> Agent | None:
    return AGENTS.get(name)


def describe_all() -> list[dict]:
    return [AGENTS[n].describe() for n in ORDER if n in AGENTS]
