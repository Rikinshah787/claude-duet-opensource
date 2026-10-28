"""Agents, the orchestrator, and catalog integrity."""
from __future__ import annotations

import pytest

from duet.agents.base import AgentContext
from duet.agents.planner import PlannerAgent
from duet.agents.registry import AGENTS, ORDER
from duet.connectors import catalog
from duet.connectors.catalog import CATALOG
from duet.models import RiskTier


class TestCatalogIntegrity:
    """A malformed catalog entry silently disables a safety declaration."""

    def test_every_field_is_the_right_shape(self):
        for spec in CATALOG:
            assert isinstance(spec.writes, tuple), f"{spec.key}: writes must be a tuple"
            assert isinstance(spec.read_scopes, tuple), f"{spec.key}: read_scopes"
            assert isinstance(spec.write_scopes, tuple), f"{spec.key}: write_scopes"
            assert isinstance(spec.signal_kinds, tuple), f"{spec.key}: signal_kinds"

    def test_keys_are_unique(self):
        keys = [c.key for c in CATALOG]
        assert len(keys) == len(set(keys))

    def test_every_category_is_declared(self):
        for spec in CATALOG:
            assert spec.category in catalog.CATEGORIES, f"{spec.key}: {spec.category}"

    def test_every_write_action_declares_a_risk(self):
        for spec in CATALOG:
            for action in spec.writes:
                assert isinstance(action.risk, RiskTier), f"{spec.key}.{action.key}"

    def test_colours_are_valid_hex(self):
        for spec in CATALOG:
            assert spec.color.startswith("#") and len(spec.color) == 7, spec.key

    def test_money_movers_are_never_merely_safe(self):
        """A verb that moves money must be at least SENSITIVE."""
        dangerous = {
            "issue_refund", "send_payment", "transfer_balance", "pay_bill",
            "submit_payroll", "sign_document",
        }
        for spec in CATALOG:
            for action in spec.writes:
                if action.key in dangerous:
                    assert action.risk in {RiskTier.SENSITIVE, RiskTier.FORBIDDEN}, (
                        f"{spec.key}.{action.key} is {action.risk}"
                    )

    def test_sending_is_never_safe(self):
        """Anything that reaches another person needs a human first."""
        for spec in CATALOG:
            for action in spec.writes:
                if action.key in {"send_email", "post_message", "publish_post",
                                  "send_campaign", "send_invoice"}:
                    assert action.risk is not RiskTier.SAFE, f"{spec.key}.{action.key}"

    def test_catalog_covers_the_advertised_surface(self):
        """The product claims breadth; this is the floor."""
        assert len(CATALOG) >= 50
        required = {
            "gmail", "slack", "google_calendar", "stripe", "quickbooks", "gusto",
            "hubspot", "salesforce", "jira", "linear", "github", "notion",
            "shopify", "docusign", "dropbox", "zoom", "teams", "outlook",
        }
        assert required <= {c.key for c in CATALOG}


class TestPlanner:
    @pytest.mark.parametrize(
        "ask,expected",
        [
            ("build me a revenue dashboard", "analytics"),
            ("reschedule my 2pm", "productivity"),
            ("draft a reply to Dana", "communication"),
            ("create a Jira ticket for the retry bug", "automation"),
            ("what's the background on Northwind", "research"),
        ],
    )
    def test_routing(self, ctx, ask, expected):
        ctx.intent = ask
        plan = PlannerAgent().run(ctx).output["plan"]
        assert any(step["agent"] == expected for step in plan), plan

    def test_writing_is_always_preceded_by_research(self, ctx):
        """Drafting without grounding is how an assistant invents facts."""
        ctx.intent = "draft a reply to Dana"
        plan = PlannerAgent().run(ctx).output["plan"]
        agents = [s["agent"] for s in plan]
        assert agents.index("research") < agents.index("communication")

    def test_composite_request_fans_out(self, ctx):
        ctx.intent = "prep me for the board meeting"
        plan = PlannerAgent().run(ctx).output["plan"]
        assert len(plan) >= 3

    def test_malformed_model_output_is_rejected(self):
        from duet.agents.planner import _validate

        assert _validate({"steps": [{"agent": "rm -rf", "instruction": "x"}]}) is None
        assert _validate({"steps": []}) is None
        assert _validate("not a dict") is None
        assert _validate({"steps": [{"agent": "research", "instruction": "ok"}]}) is not None

    def test_plan_length_is_capped(self):
        from duet.agents.planner import _validate

        out = _validate(
            {"steps": [{"agent": "research", "instruction": f"s{i}"} for i in range(20)]}
        )
        assert len(out["steps"]) <= 5


class TestRegistry:
    def test_all_eight_agents_are_registered(self):
        assert len(ORDER) == 8
        assert set(ORDER) <= set(AGENTS)

    def test_each_agent_describes_itself(self):
        for name in ORDER:
            spec = AGENTS[name].describe()
            assert spec["role"] and spec["capabilities"]


class TestOrchestrator:
    def test_trace_is_persisted_step_by_step(self, seeded):
        r = seeded.post("/api/thread", json={"text": "Reply to Priya at Northwind"}).json()
        _run(r["task"]["id"])

        steps = seeded.get(f"/api/tasks/{r['task']['id']}").json()["steps"]
        assert steps[0]["agent"] == "planner"
        assert steps[-1]["agent"] == "security"
        assert all(s["status"] in {"ok", "error"} for s in steps)

    def test_a_failing_specialist_does_not_kill_the_task(self, seeded, monkeypatch):
        from duet.agents.workers import ResearchAgent

        def boom(self, ctx):
            raise RuntimeError("connector exploded")

        monkeypatch.setattr(ResearchAgent, "run", boom)

        r = seeded.post("/api/thread", json={"text": "Reply to Priya at Northwind"}).json()
        _run(r["task"]["id"])

        detail = seeded.get(f"/api/tasks/{r['task']['id']}").json()
        assert detail["task"]["status"] != "failed"
        assert any(s["status"] == "error" for s in detail["steps"])

    def test_analytics_computes_from_real_signals(self, seeded):
        board = seeded.get("/api/dashboards/executive").json()
        assert board["tiles"]
        overdue = next(t for t in board["tiles"] if "Overdue" in t["label"])
        assert "$" in overdue["value"]
        # Receivables are summed from observed invoices, so this is real.
        assert board["finance"]["overdue_total"] > 0

    def test_forecast_refuses_to_invent_a_cash_position(self, seeded):
        """A fabricated runway renders identically to a real one.

        With no figures configured the forecast must decline rather than guess,
        because an operator would act on the number either way.
        """
        forecast = seeded.get("/api/dashboards/executive").json()["forecast"]
        assert forecast["configured"] is False
        assert forecast["series"] == []
        assert forecast["weeks_remaining"] is None
        assert "won't guess" in forecast["note"]

    def test_forecast_projects_once_figures_are_supplied(self, seeded):
        seeded.patch(
            "/api/settings",
            json={"finance": {"starting_cash": 420000, "monthly_burn": 68000}},
        )
        forecast = seeded.get("/api/dashboards/executive").json()["forecast"]
        assert forecast["configured"] is True
        assert forecast["series"]
        assert forecast["weeks_remaining"] > 0
        assert forecast["assumptions"]["monthly_burn"] == 68000
        # The projection must use the user's numbers, not the old hardcoded ones.
        assert forecast["assumptions"]["starting_cash"] == 420000

    def test_conversational_input_does_not_spawn_a_task(self, seeded):
        out = seeded.post("/api/thread", json={"text": "thanks"}).json()
        assert out["task"] is None

    def test_status_question_answers_without_a_task(self, seeded):
        out = seeded.post("/api/thread", json={"text": "what do I need to know?"}).json()
        assert out["task"] is None
        assert out["reply"]["content"]


class TestMemory:
    def test_explicit_preference_is_remembered(self, ctx):
        from duet.agents.memory import capture_preference

        assert capture_preference(ctx, "Always use my first name in replies") is not None

    def test_passing_remark_is_not_remembered(self, ctx):
        from duet.agents.memory import capture_preference

        assert capture_preference(ctx, "send that to Dana") is None

    def test_recall_finds_the_relevant_memory(self, ctx):
        from duet.agents.memory import recall, remember

        remember(ctx, "Northwind is the largest customer at $84k ARR", kind="long")
        remember(ctx, "Prefers oat milk", kind="long")

        hits = recall(ctx, "tell me about Northwind", limit=2)
        assert hits
        assert "Northwind" in hits[0].content

    def test_duplicate_preferences_collapse(self, ctx):
        from duet.agents.memory import remember

        a = remember(ctx, "Always be brief", kind="long", key="pref:brief")
        b = remember(ctx, "Always be very brief", kind="long", key="pref:brief")
        assert a.id == b.id


def _run(task_id: str) -> None:
    from duet.core.orchestrator import run_task
    from duet.db import session_scope
    from duet.models import Task

    with session_scope() as session:
        run_task(session, session.get(Task, task_id))


class TestQuestionsGetAnswered:
    """A task that proposes no action was a question, and must return prose.

    Found by running it: asking "what's been waiting on me longest?" completed
    successfully and posted "Done: <the question>" back. The work happened and
    the user learned nothing.
    """

    def test_a_question_produces_an_answer_not_a_status_line(self, seeded):
        from duet.core.orchestrator import run_task
        from duet.db import session_scope
        from duet.models import Task

        r = seeded.post(
            "/api/thread", json={"text": "What is the status of the Northwind account?"}
        ).json()
        with session_scope() as s:
            run_task(s, s.get(Task, r["task"]["id"]))

        last = seeded.get("/api/thread?limit=5").json()["messages"][-1]
        assert not last["content"].startswith("Done:"), last["content"]
        assert last["meta"].get("answered") is True
        # The answer must carry real content from the findings.
        assert len(last["content"]) > 40

    def test_an_action_task_still_reports_what_it_held(self, seeded):
        """Answering must not swallow the approval summary."""
        from duet.core.orchestrator import run_task
        from duet.db import session_scope
        from duet.models import Task

        r = seeded.post(
            "/api/thread", json={"text": "Reply to Priya at Northwind about the failures"}
        ).json()
        with session_scope() as s:
            run_task(s, s.get(Task, r["task"]["id"]))

        msgs = seeded.get("/api/thread?limit=6").json()["messages"]
        assert any("Nothing has been sent" in m["content"] for m in msgs)

    def test_answer_digest_includes_ages(self):
        """Without dates the model caveats an answer it had the data to give."""
        import inspect

        from duet.core import orchestrator

        src = inspect.getsource(orchestrator._compose_answer)
        assert "_age" in src and "when" in src
