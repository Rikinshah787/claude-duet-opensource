# 01 - Architecture

Duet is a local-first proactive executive assistant. It watches connected apps,
scores what it finds, decides whether anything is worth interrupting you for,
plans multi-step work, and holds every outbound action behind a human gate.

Everything in this document maps to a file under `apps/api/duet/`. Where a
component is not yet written, it says so.

---

## 1. Component map

```
                                 ┌──────────────────────────────────────┐
                                 │  apps/web  (Next.js 15 / React 19)   │
                                 │  Thread · Brief · Approvals ·        │
                                 │  Activity · Connectors · Audit       │
                                 └───────────────┬──────────────────────┘
                                                 │ HTTP /api/*  (rewrite in
                                                 │ apps/web/next.config.mjs)
                                                 ▼
  ┌──────────────────────────────────────────────────────────────────────────┐
  │  apps/api  -  FastAPI, 127.0.0.1:8787                                    │
  │                                                                          │
  │  duet/main.py         app factory, CORS, lifespan, scheduler start      │
  │  duet/api/deps.py     current_user, require_role (RBAC)                 │
  │  duet/api/thread.py   /thread, /briefing/*                              │
  │  duet/api/work.py     /tasks/*, /approvals/*, /signals/*                │
  │  duet/api/apps.py     /connectors/*, /settings/*                        │
  │  duet/api/system.py   /health, /me, /agents, /audit/*, /sync, /admin/*  │
  │                                                                          │
  │  ┌─ duet/core/ ────────────────────────────────────────────────────────┐ │
  │  │                                                                     │ │
  │  │  scheduler.py      the autonomous loop    4 cadences, daemon thread │ │
  │  │  monitor.py        proactive sweep        read-only, scores signals │ │
  │  │  priority.py       priority engine        8 factors → disposition   │ │
  │  │  briefing.py       morning brief          sections from signals     │ │
  │  │  orchestrator.py   task pipeline          plan → dispatch → review  │ │
  │  │  executor.py       THE ONLY WRITER        decide/execute/revert     │ │
  │  │  injection.py      prompt-injection screen  scan/sanitize/wrap      │ │
  │  │  redaction.py      egress gate            STRICT / GUARDED tiers    │ │
  │  │  vault.py          credential isolation   Fernet + CredentialHandle │ │
  │  │  sandbox.py        isolated execution     subprocess/docker/browser │ │
  │  │  audit.py          hash-chained log       record() / verify_chain() │ │
  │  │  telemetry.py      observability          collect() / prometheus()  │ │
  │  └─────────────────────────────────────────────────────────────────────┘ │
  │                                                                          │
  │  ┌─ duet/agents/ ──────────────────────────────────────────────────────┐ │
  │  │  planner → research · communication · productivity · analytics ·    │ │
  │  │            automation                                               │ │
  │  │  security  (the only agent that can clear a Proposal)               │ │
  │  │  memory    (recall, preference capture, episodic history)           │ │
  │  └─────────────────────────────────────────────────────────────────────┘ │
  │                                                                          │
  │  ┌─ duet/connectors/ ──────────────────────────────────────────────────┐ │
  │  │  catalog.py    58 ConnectorSpec entries + WriteAction risk tiers    │ │
  │  │  base.py       Connector protocol: fetch() read / execute() write   │ │
  │  │  registry.py   resolve(key) → live adapter, else Unavailable        │ │
  │  │  live/         6 real adapters: github_cli · imap_mail · linear ·   │ │
  │  │                notion · slack · stripe                             │ │
  │  └─────────────────────────────────────────────────────────────────────┘ │
  │                                                                          │
  │  duet/llm/agent_cli.py 5 agent CLIs behind one SPECS table               │
  │  duet/llm/client.py    agent_cli|anthropic|ollama|openai|heuristic       │
  │  duet/models.py        13 SQLAlchemy tables, 5 enums                     │
  │  duet/db.py            engine, SessionLocal, session_scope, init_db      │
  │  duet/config.py        pydantic-settings, env prefix DUET_               │
  └──────────────────────────┬───────────────────────────────────────────────┘
                             │
          ┌──────────────────┴───────────────────┐
          ▼                                      ▼
  ┌───────────────────┐              ┌──────────────────────────────┐
  │  SQLite (default) │              │  Vault:  .data/vault.enc     │
  │  .data/duet.db    │              │  Fernet, key from            │
  │  WAL, FK on       │              │  DUET_SECRET_KEY             │
  │  Postgres in      │              │  (swap seam for Vault /      │
  │  compose          │              │   1Password / AWS SM)        │
  └───────────────────┘              └──────────────────────────────┘
                             │
                             ▼
      ┌──────────────────────────────────────────────────┐
      │  Model backend - resolved at startup from "auto" │
      │                                                  │
      │  1. agent_cli  an agent CLI you already have     │
      │                signed in: claude · codex ·       │
      │                gemini · cursor-agent · opencode  │
      │                (prompt on stdin, throwaway cwd)  │
      │  2. anthropic  the Claude API, needs an API key  │
      │  3. ollama     http://localhost:11434            │
      │  4. heuristic  none at all - deterministic       │
      │                                                  │
      │  openai: any OpenAI-compatible /v1 endpoint,     │
      │          reachable only by naming it             │
      └──────────────────────────────────────────────────┘
```

The model backend is the one component with no fixed identity: `auto` is the
default and resolves, in that order, to whatever the machine turns out to have.
Nothing in the system is tied to one vendor - adding a CLI is a row in
`agent_cli.SPECS`.

Two things are deliberately absent from that diagram:

- **No queue broker.** `settings.redis_url` exists and defaults to `None`; the
  fallback is in-process. Nothing in the code reads it yet.
- **No vector database.** `settings.qdrant_url` / `qdrant_collection` exist and
  default to `None` / `"duet_memory"`, but nothing reads them.
  `Memory.embedding` is a JSON column and `agents/memory.py::embed()` is a
  96-dimension hashing projection.

One component is in the tree but off the main path: `core/sandbox.py` provides
isolated execution (`subprocess` | `docker` | `browser`) for work that needs a
filesystem, a shell or a logged-in web session. No agent dispatches to it yet;
`GET /api/agents` reports `sandbox.available()` so the capability is never
silently assumed. See [docs/06-security.md](06-security.md#7b-c16-sandboxed-execution).

---

## 2. Layering rules

These are the invariants the code is built around. Three of them are mechanical;
one is currently a convention enforced by code review rather than by a type.

### Rule 1 - agents may only `propose()`

`apps/api/duet/agents/base.py` defines `Proposal`. A specialist that wants to
change the world outside this process returns `AgentResult.proposals`. It does
not import `connectors.registry`, does not hold a `CredentialHandle`, and has no
reference to anything that can make an outbound call.

Verified by inspection: of the five worker agents in
`apps/api/duet/agents/workers.py`, `CommunicationAgent`, `ProductivityAgent` and
`AutomationAgent` emit proposals; `ResearchAgent` and `AnalyticsAgent` emit only
`output` and `artifacts`. None of them import a connector adapter.

### Rule 2 - only the Security agent clears a proposal

`apps/api/duet/core/orchestrator.py::_security_review()` is the only caller that
turns a list of proposals into a cleared list, and it does so by running
`agents/security.py::SecurityAgent`. The function returns
`result.proposals` - the proposals the Security agent chose to pass through -
and `_create_approvals()` only ever sees that list.

`SecurityAgent.review()` runs four checks in order and can only ever *hold*,
*block* or *pass through*. It cannot lower a risk tier: line
`risk = declared` overwrites whatever the proposing agent claimed with
`catalog.risk_for(...)`, and `catalog.risk_for()` returns `RiskTier.SENSITIVE`
for any verb it does not recognise.

### Rule 3 - only the executor acts

`apps/api/duet/connectors/base.py` splits the protocol:

```python
def fetch(self, ctx: FetchContext) -> tuple[list[RawSignal], dict[str, Any]]:
    """Return new observations and the updated cursor. Read-only."""

def execute(self, action: str, payload: dict[str, Any], ctx: FetchContext) -> ActionResult:
    """Perform one approved write. Called only by the approval executor."""
```

`fetch()` has exactly one caller: `core/monitor.py::_sweep_connection()`.

`execute()` has exactly two callers, both in `core/executor.py`:
`execute()` (the forward path) and `revert()` (the undo path). Nothing else in
the tree calls it. That is the claim to audit: if you want to know whether the
assistant can send something without asking, read one file.

`core/executor.py` re-checks at execution time, because approval and execution
are separated in time:

```python
def _preflight(db, approval) -> None:
    if approval.action_type in FORBIDDEN_ACTIONS:  ...refuse
    if catalog.risk_for(key, action) is RiskTier.FORBIDDEN:  ...refuse
    if conn is None:                               ...refuse  "no longer connected"
    if not conn.enabled or conn.status != "connected":  ...refuse
    if approval.expires_at < now:                  ...refuse  "expired before acted on"
```

`decide()` refuses outright if `approval.status != "pending"`, so a double-click
on Approve cannot execute twice.

### Rule 4 - the sweep never writes

`core/monitor.py` imports `FetchContext` and `CredentialHandle` and calls
`adapter.fetch()`. It does not import `ActionResult` and never calls
`execute()`. This is what makes unattended operation defensible: the autonomous
loop can only observe.

---

## 3. The request path: a user task

```
POST /api/thread  {"content": "chase the overdue invoices"}
  │
  ├─ Message(role="user") persisted
  │
  └─ orchestrator.create_task(db, user, intent, origin="user")
       │   Task(status=QUEUED, origin="user")
       │   memory.capture_preference(ctx, intent)     ← only if the utterance
       │                                                matches a PREFERENCE_MARKER
       └─ orchestrator.run_task(db, task)
            │
            │  status → PLANNING, started_at set, attempts += 1
            │  AgentContext(db, user, llm, task_id, intent)
            │  ctx.recall = memory.recall(ctx, intent, limit=6)   ← 6 memories
            │  audit.record(action="task.started", actor="agent:planner")
            │
            ├─ _plan()                      TaskStep idx=0, agent="planner"
            │    PlannerAgent.run(ctx)
            │      heuristic plan from COMPOSITES / ROUTES
            │      if llm.provider != "heuristic": ask the model, then _validate()
            │    task.plan = [{agent, instruction}, ...]
            │    task.lead_agent = plan[0]["agent"]
            │
            │  status → RUNNING
            │
            ├─ _execute_plan()              TaskStep idx=1..N, one per plan step
            │    for each step:
            │      ctx.step_input = {"instruction": ...}
            │      ctx.prior_outputs = outputs so far   ← how research feeds comms
            │      agent.run(ctx)  (exceptions caught per agent; one bad
            │                       specialist does not kill the task)
            │      collect result.proposals / artifacts / tokens
            │      db.commit()   ← the trace is readable mid-flight
            │
            ├─ _security_review()           TaskStep idx=90, agent="security"
            │    ctx.step_input = {"proposals": [...],
            │                      "user_directed": task.origin == "user"}
            │    SecurityAgent.run(ctx) → (cleared, blocked[])
            │
            ├─ _create_approvals()
            │    one Approval row per cleared proposal
            │    expires_at = now + settings.approval_expiry_minutes (default 720)
            │    audit.record(action="approval.requested", actor="agent:security")
            │
            ├─ status → AWAITING_APPROVAL  (or DONE when there are no approvals)
            ├─ _post_result_message()       Message(kind="approval"|"task_update")
            ├─ memory.record_episode()      "[date] <title> - awaiting approval"
            └─ audit.record(action="task.finished")
```

On exception, `run_task` rolls back, re-queues if `task.attempts <
settings.task_retry_limit` (default 3), otherwise marks `FAILED`, posts a
message that says *"Nothing was sent or changed."*, and records
`action="task.failed"` with a truncated traceback.

### Sequence walkthrough: "chase the overdue invoices"

Traced against the scripted scenario in `apps/api/tests/fixtures/scenario.py`
(a test fixture - the product ships no sample data), this is what actually
happens.

| # | Actor | What happens | Evidence in code |
|---|-------|--------------|------------------|
| 1 | API | `Message(role="user", content="chase the overdue invoices")` | — |
| 2 | orchestrator | `create_task(origin="user")`. `capture_preference` finds no marker word, writes nothing. | `agents/memory.py::PREFERENCE_MARKERS` |
| 3 | memory | `recall(ctx, intent, 6)` ranks `Memory` rows by `cosine(embed(q), m.embedding) * 0.6 + salience * 0.3 + recency * 0.1`, `+0.12` for `kind == "long"`. Empty on a fresh install. | `agents/memory.py::recall` |
| 4 | planner | `COMPOSITES[1]` matches `\b(chase\|collect\|follow up).{0,24}\b(invoice\|payment\|overdue\|receivable)\b`. Plan is fixed: `research` then `communication`. No model turn is spent. | `agents/planner.py::COMPOSITES` |
| 5 | research | `_recent_signals` loads the last 220 `Signal` rows, `_rank` scores keyword overlap `hits * 10 + score * 0.25`. The two `invoice_overdue` rows (`qb-overdue-401` $18,400 / 41 days, `qb-overdue-402` $6,100 / 12 days) plus the Gmail reminder rank top. Returns `findings[]`. | `agents/workers.py::ResearchAgent` |
| 6 | communication | `_pick_target` looks for a capitalised name in the instruction, finds none, falls back to the first finding with an `actor`. `_channel_for` maps connector `quickbooks` → not in `{gmail, outlook}` → `("email", "gmail")`. `_draft` has no model (default provider), so `_template_body` fires the `"invoice" in kind` branch. One `Proposal(action_type="send_email", connector_key="gmail", risk=REVIEW, reversible=False)`. | `agents/workers.py::CommunicationAgent` |
| 7 | security | Check 1: `send_email` is not in `FORBIDDEN_ACTIONS`. Check 2 (risk): `catalog.risk_for("gmail", "send_email")` → `REVIEW`, overriding the agent's claim. Check 3 (scope): a `Connection` row for `gmail` exists, `enabled`, `status == "connected"`; `spec.write_scopes` is `("gmail.compose", "gmail.send", "gmail.modify")` and intersects the granted scopes. Check 4 (egress): `_renderable()` concatenates subject+body, `check_egress(..., user_directed=True)` finds no detector hit → allowed. | `agents/security.py::review` |
| 8 | orchestrator | `Approval(action_type="send_email", risk_tier="review", reversible=False, status="pending", expires_at=+12h)`. `preview` holds `{channel, to, subject, body, in_reply_to}` - exactly what will happen. `payload` holds the call. | `core/orchestrator.py::_create_approvals` |
| 9 | orchestrator | Task → `AWAITING_APPROVAL`. Message: *"I've prepared 1 thing for "Chase the overdue invoices". Nothing has been sent or changed yet."* | `_post_result_message` |
| 10 | audit | Three chained rows: `task.started`, `approval.requested`, `task.finished`. Each commits to the previous hash. | `core/audit.py::record` |
| 11 | **you** | `POST /api/approvals/{id}/approve` → `executor.decide(approved=True)`. Status → `approved`, audit `approval.approved`, then `_preflight()` re-checks the five conditions and `adapter.execute("send_email", payload, ctx)` runs. Status → `executed`, `execution_result` stored, audit `action.executed`, a thread message reports the adapter's own summary, and `_settle_task()` closes the task once nothing else on it is pending. | `core/executor.py` |

Note what step 6 did *not* do: it did not send anything, did not open a socket,
and did not resolve a credential. The draft is text in a `Proposal` dataclass
until a human says yes.

---

## 4. The proactive path: a sweep

```
scheduler tick (every settings.sweep_interval_s, default 900s)
  │
  └─ monitor.sweep_user(db, user)
       │
       │  SELECT Connection WHERE user_id=? AND enabled AND monitored
       │                          AND status='connected'
       │  SELECT PolicyRule WHERE user_id=? AND enabled
       │  vips = {r.pattern for r in rules if r.kind == "vip"}
       │  history = memory.behaviour_history(db, user.id)      ← handled/dismissed
       │  local_hour = ZoneInfo(user.timezone).hour            ← for quiet hours
       │
       ├─ for each connection:  _sweep_connection()
       │     adapter = registry.resolve(conn.connector_key)
       │       → REAL[key]() if registered, else UnavailableConnector(key)
       │         (which returns ([], cursor) - it never invents data)
       │     ctx = FetchContext(user_id, connection_id, since=conn.last_sync_at,
       │                        cursor=conn.cursor, credential=CredentialHandle|None)
       │     raw_signals, cursor = adapter.fetch(ctx)      ← READ ONLY
       │     conn.cursor = cursor
       │
       │     for each RawSignal:
       │       threat = injection.screen_signal(raw.title, raw.body)
       │       if threat.suspicious:               ← BEFORE scoring, before storing
       │         raw.hints["injection"] = threat.as_dict()
       │         log.warning(...) and audit "security.injection_detected"
       │                                 (outcome="hostile" or "flagged")
       │
       │       verdict = priority.score_signal(kind, connector_key, title, body,
       │                                       actor, occurred_at, hints,
       │                                       rules, vips, history,
       │                                       local_hour, quiet_hours)
       │       Signal(score=..., factors={name: pts}, reasons=[...],
       │              disposition=verdict.disposition.value, state="new")
       │       db.flush()        ← uq_conn_external raises IntegrityError on a
       │                            repeat; caught, rolled back, skipped.
       │                            THIS is the dedupe. See docs/02-data-model.md.
       │
       │     on any exception: conn.last_error, conn.status = "error",
       │                       the other connections still sweep
       │     db.commit()  per connection
       │
       ├─ _maybe_interrupt()
       │     urgent = [s for s in created if disposition == NOTIFY_NOW]
       │     sort by score desc, take MAX_INTERRUPTS_PER_SWEEP (= 1)
       │     one Message(proactive=True) naming the lead item, with
       │       meta={"score", "reasons"[:4], "connector", "also"[:3]}
       │     the surfaced signals get state="surfaced", surfaced_at=now
       │
       └─ audit.record(action="monitor.sweep",
                       detail={connections, new_signals, notified, errors})
```

Two caps are worth naming because they are product decisions, not limits:

- **`MAX_INTERRUPTS_PER_SWEEP = 1`** (`core/monitor.py`). Even when five things
  clear the notify bar, you get one grouped message. The trade-off: you may
  learn about the second-most-urgent item a few minutes late. Chosen because a
  burst of five notifications trains a user to ignore the sixth, and an ignored
  proactive assistant has negative value.
- **`sweep_interval_s = 900`** (`config.py`). Minutes, not seconds. Every sweep
  costs connector calls and - once a model is configured - model turns. The
  cost model is in [docs/09-roadmap.md](09-roadmap.md); the cadence rationale is
  in [docs/07-workflows.md](07-workflows.md).

### Where the brief fits

`core/briefing.py::generate()` is a separate pass, not part of the sweep. It
reads back `Signal` rows from the last `lookback_h` hours (default 24) whose
disposition is `NOTIFY_NOW` or `BRIEFING` and whose state is not `dismissed`,
buckets them into the five `SECTIONS`, prepends a *"Needs your approval"*
section built from pending `Approval` rows, and appends *"What I'd do first"*
from `_recommendations()`.

The model, if configured, writes only `headline` - the two-sentence opener.
Section membership and ordering are deterministic. The reason is stated in the
module docstring: selection has to be auditable, so selection is code.

`core/scheduler.py::Scheduler._maybe_brief()` fires it: for every user with
`briefing_optin`, once their `_local_hour() >= settings.briefing_hour`, if no
`Briefing` row exists for today's `for_date`. One brief per user per day, and
the existence check is the idempotency guard - there is no "already sent" flag
to get out of sync.

`monitor.due_for_sweep(conn, interval_s)` exists as a per-connection staleness
helper but is not called by the scheduler, which sweeps every monitored
connection on each pass. See [docs/07-workflows.md](07-workflows.md).

---

## 5. Data flow summary

| Stage | Produces | Table | Module |
|-------|----------|-------|--------|
| Observe | `RawSignal` | — | `connectors/*.fetch()` |
| Screen | `InjectionReport` → `hints["injection"]` | `signals.raw` | `core/injection.py` |
| Score | `Verdict` (score, disposition, factors, reasons) | `signals` | `core/priority.py` |
| Interrupt or defer | proactive `Message` | `messages` | `core/monitor.py` |
| Summarise | `Briefing` + sections | `briefings` | `core/briefing.py` |
| Plan | plan steps | `tasks.plan` | `agents/planner.py` |
| Work | `AgentResult` per step | `task_steps` | `agents/workers.py` |
| Gate | `SecurityVerdict` | — | `agents/security.py` |
| Hold | `Approval` (pending) | `approvals` | `core/orchestrator.py` |
| Act | `ActionResult` | `approvals.execution_result` | `core/executor.py` |
| Remember | `Memory` | `memories` | `agents/memory.py` |
| Record | hash-chained row | `audit_log` | `core/audit.py` |

---

## 6. Process model

One FastAPI process, plus one daemon thread. `duet/main.py` starts the
scheduler in the `lifespan` hook unless `DUET_DISABLE_SCHEDULER` is set to
`1` / `true` / `yes` - the escape hatch for tests and CLI use, *"where an
autonomous background loop is surprising."* It stops on shutdown.

`db.py` installs SQLite pragmas on connect:

```python
PRAGMA journal_mode=WAL
PRAGMA foreign_keys=ON
PRAGMA busy_timeout=5000
```

The comment is explicit about why: *"Without this the scheduler thread and the
API thread fight over the same SQLite file."* That is the concurrency model -
request handlers use `get_db()` (a FastAPI dependency generator), background
work uses `session_scope()` (a commit-or-rollback context manager). WAL plus a
5-second busy timeout is what makes two writers on one file survivable.

The same SQLAlchemy models run on Postgres unchanged; `_make_engine()` only
branches to add `check_same_thread=False` for SQLite.

`settings.max_parallel_tasks` (default 4) bounds how many `QUEUED` tasks the
scheduler claims per pass (`scheduler._run_queued_tasks()`). Note that it then
runs them **sequentially** in that thread - the name describes the batch size,
not concurrency. `main.py` also installs a catch-all exception handler that logs
the traceback and returns `{"detail": "<TypeName>. Check the server log."}` with
status 500, so a stack trace never reaches the client.

---

## 7. Trade-offs taken

| Decision | Alternative | Why this side |
|----------|-------------|---------------|
| Heuristic provider is the default | Require a model to run at all | The sweep, scoring, briefing, approval gate and audit chain are deterministic code. The product does useful work on `git clone` with no 7B download. The model buys fluency and open-ended decomposition, not correctness. |
| Priority is an explainable weighted sum | A learned ranker | Every factor returns points *and* a sentence, so the UI can answer "why did you tell me about this?" with the real reason. `historical_factor()` is the documented slot where a trained model goes. |
| Risk declared in the catalog, not the agent | Agents assert their own risk | `agents/security.py` overwrites the agent's claim with `catalog.risk_for()`. One place to audit, and a compromised or confused agent cannot talk its way down a tier. |
| Unknown verb → `SENSITIVE` | Unknown verb → `SAFE` | Named in `catalog.risk_for()`: *"Defaulting an unrecognised action to SAFE would mean a typo silently skips the approval gate."* |
| Dedupe via a DB unique constraint | Application-level seen-set | `uq_conn_external` makes the dedupe a property of the schema, so it survives a restart, a crash mid-sweep and two concurrent sweeps. |
| One grouped interrupt per sweep | Notify on everything urgent | See above. |
| SQLite by default | Postgres from the start | Zero dependencies to evaluate the product. Identical models mean the swap is a connection string. |

---

## 8. Reading order for a new contributor

1. `apps/api/duet/models.py` - the whole domain in one file.
2. `apps/api/duet/core/priority.py` - the product's point of view, as numbers.
3. `apps/api/duet/agents/base.py` + `agents/planner.py` - the agent contract.
4. `apps/api/duet/agents/security.py` - the gate everything passes through.
5. `apps/api/duet/core/orchestrator.py` - how the pieces are ordered.
6. `apps/api/duet/connectors/base.py` + `connectors/live/github_cli.py` - the edge.

Then [docs/02-data-model.md](02-data-model.md).
