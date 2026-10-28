# 03 - Agents

Eight agents live in `apps/api/duet/agents/`. They are narrow, stateless, and
synchronous. An agent receives an `AgentContext`, does one kind of work, and
returns an `AgentResult`.

The constraint that makes the rest of the system defensible is stated in
`agents/base.py`:

> An agent that wants to change the world outside this process can only
> `propose()`. A proposal becomes an `Approval` row; nothing happens until a
> human decides. There is no agent-side bypass.

| # | `name` | Class | File | Can propose? |
|---|--------|-------|------|--------------|
| 1 | `planner` | `PlannerAgent` | `agents/planner.py` | no |
| 2 | `research` | `ResearchAgent` | `agents/workers.py` | no |
| 3 | `communication` | `CommunicationAgent` | `agents/workers.py` | **yes** |
| 4 | `productivity` | `ProductivityAgent` | `agents/workers.py` | **yes** |
| 5 | `analytics` | `AnalyticsAgent` | `agents/workers.py` | no |
| 6 | `automation` | `AutomationAgent` | `agents/workers.py` | **yes** |
| 7 | `security` | `SecurityAgent` | `agents/security.py` | no - it *clears* |
| 8 | `memory` | `MemoryAgent` | `agents/memory.py` | no |

Registered in `agents/registry.py`:

```python
AGENTS: dict[str, Agent] = {"planner": PlannerAgent(), ...}
ORDER = ("planner", "research", "communication", "productivity",
         "analytics", "automation", "security", "memory")

def get_agent(name: str) -> Agent | None: ...
def describe_all() -> list[dict]: ...
```

Instances are module-level singletons. That is only safe because agents hold no
per-request state - everything comes in on the context. Adding mutable
instance attributes to an agent is a correctness bug, not a style preference.

`ORDER` is also the order the Agent Health view lists them in.

---

## 1. The contract

### `AgentContext`

```python
@dataclass
class AgentContext:
    db: Session
    user: User
    llm: "LLMClient"
    task_id: str | None = None
    intent: str = ""                      # the user's ask, verbatim
    step_input: dict[str, Any] = {}       # this step's instruction / params
    recall: list[str] = []                # Memory contents, pre-fetched
    prior_outputs: dict[str, Any] = {}    # {agent_name: AgentResult.output}
```

`prior_outputs` is how a later step reads an earlier one.
`orchestrator._execute_plan()` rebinds it before each step, so
`CommunicationAgent` reaching for `ctx.prior_outputs.get("research", {})` gets
whatever `ResearchAgent` produced in this same task.

`recall` is populated **once**, in `run_task`, before the plan is built:

```python
ctx.recall = [m.content for m in recall(ctx, task.intent, limit=6)]
```

Agents read it through the `Agent._with_recall()` helper, which prefixes the
prompt with a `What you already know about this user:` block capped at 8 lines.

### `AgentResult`

```python
@dataclass
class AgentResult:
    ok: bool
    summary: str                          # one line, shown in the Activity trace
    output: dict[str, Any] = {}           # structured, redacted before persisting
    proposals: list[Proposal] = []        # world-changing actions, unexecuted
    artifacts: list[dict[str, Any]] = []  # dashboards, drafts, docs
    memories: list[dict[str, Any]] = []   # declared; orchestrator ignores it today
    error: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
```

What the orchestrator does with each field (`_execute_plan`, `_end_step`):

- `ok=False` → the step is marked `error`; `output`, `proposals` and `artifacts`
  are **discarded**. A failed agent contributes nothing.
- `summary` → `TaskStep.summary`, truncated to 500.
- `output` → `TaskStep.output`, after `redaction.redact()`.
- `proposals` → accumulated, then handed to the Security agent as a batch.
- `artifacts` → accumulated onto `Task.artifacts`.
- `tokens_in` / `tokens_out` → summed onto the task.
- `memories` → currently unread. Agents that want to persist a memory call
  `memory.remember()` directly.

An agent raising is caught per-step in `_execute_plan`; the orchestrator
synthesises `AgentResult(ok=False, ...)` and continues with the next step. One
bad specialist does not kill the task.

### `Proposal`

```python
@dataclass
class Proposal:
    action_type: str                  # must match a WriteAction.key in the catalog
    title: str
    rationale: str                    # why the assistant wants this
    connector_key: str | None = None
    payload: dict[str, Any] = {}      # what the executor will run
    preview: dict[str, Any] = {}      # what the human will read
    risk: RiskTier | None = None      # Security fills this in if unset
    reversible: bool = True
```

Three things about `risk`:

1. If the agent leaves it `None`, `SecurityAgent.review()` starts from
   `RiskTier.SENSITIVE` - the pessimistic default.
2. If the agent sets it **and** `connector_key` is set, the catalog's value
   **overwrites** it unconditionally (`risk = declared`). An agent cannot
   declare itself safe.
3. The only case where an agent's `risk` survives is a proposal with no
   `connector_key`, which nothing currently produces.

`payload` and `preview` are deliberately separate. `preview` is prose for a
person; `payload` is arguments for a machine. Keeping them apart means the human
reviews something readable instead of a serialised API call.

### Base class

```python
class Agent:
    name: str = "agent"
    role: str = ""
    capabilities: tuple[str, ...] = ()

    def run(self, ctx: AgentContext) -> AgentResult: ...
    def _with_recall(self, ctx, prompt: str) -> str: ...
    def describe(self) -> dict[str, Any]: ...
```

`describe()` returns `{"name", "role", "capabilities"}` and is what
`registry.describe_all()` surfaces to the Agent Health view.

---

## 2. Agent 1 - Planner

**File:** `agents/planner.py` · **Role:** `"Executive Planner"` ·
**Capabilities:** goal decomposition, prioritisation, action planning

**Input:** `ctx.intent`. **Output:**
`{"plan": [{"agent", "instruction"}], "title": str, "planner": <source>}`
where `<source>` is `"heuristic"` or the LLM provider name.

The planner runs the heuristic path **always**, then, only if
`ctx.llm.provider != "heuristic"`, asks the model and replaces the plan if the
model's output validates. The fallback is not a degraded mode: as the module
docstring puts it, *"draft a reply to Dana" has exactly one sensible plan, and
spending a model turn to rediscover that wastes the user's plan limits.*

### Dispatch, exactly

`_heuristic_plan(intent)` runs in this order:

**Step 1 - COMPOSITES.** First match wins and returns immediately. These are
the requests that genuinely need more than one specialist.

| Pattern | Plan |
|---------|------|
| `\b(prep\|prepare\|ready\|brief).{0,24}\b(meeting\|call\|board\|investor\|qbr)\b` | `research` → `analytics` → `communication` |
| `\b(chase\|collect\|follow up).{0,24}\b(invoice\|payment\|overdue\|receivable)\b` | `research` → `communication` |
| `\b(fix\|resolve\|handle\|deal with).{0,24}\b(escalation\|churn\|angry\|at risk)\b` | `research` → `communication` → `automation` |

All three are `re.I`. The `.{0,24}` gap is what makes
`"prepare me for the board meeting"` match while
`"prepare the quarterly numbers, and separately there's a meeting"` does not.

The instructions are fixed strings, not the user's words. For the invoice
composite:

```
research:      "Identify every overdue invoice, its age, amount and prior contact history."
communication: "Draft a reminder for each one, escalating in tone with age."
```

**Step 2 - ROUTES.** If no composite fired, the first matching route sets the
**lead agent**. Order matters and is not alphabetical: analytics is checked
first because `"revenue report"` should not be routed to communication by the
word "report"… and communication is checked before automation because
`"email the team about the new ticket"` is a message, not a ticket.

| Order | Agent | Regex (all `re.I`) |
|-------|-------|--------------------|
| 1 | `analytics` | `\b(dashboard\|forecast\|report\|analy[sz]e\|analytics\|metrics?\|revenue\|runway\|burn\|chart\|spreadsheet\|model\|projection\|pipeline report\|kpi)\b` |
| 2 | `productivity` | `\b(schedul\w*\|calendar\|meeting\|reschedul\w*\|move (?:the\|my) \w+\|book\|conflict\|availability\|free time\|agenda\|invite)\b` |
| 3 | `communication` | `\b(draft\|reply\|respond\|email\|message\|follow[- ]?up\|write to\|send\|reach out\|chase\|remind(?:er)? to \w+)\b` |
| 4 | `automation` | `\b(create (?:a )?(?:ticket\|issue\|task\|record\|page)\|update (?:the )?(?:crm\|deal\|project\|ticket)\|file (?:a )?(?:bug\|ticket)\|log (?:it\|this\|activity)\|jira\|linear\|notion page)\b` |
| 5 | `research` | `\b(research\|find\|look up\|search\|summari[sz]e\|brief me\|what(?:'s\| is) the status\|catch me up\|background on\|who is\|compare)\b` |

**Default lead when nothing matches: `research`.** The system's reflex on an
unrecognised request is to go read, not to go write.

**Step 3 - grounding.** If the lead is `communication` or `automation`, a
`research` step is prepended:

```python
if lead in {"communication", "automation"}:
    steps.append({"agent": "research",
                  "instruction": f"Find the context needed to handle: {intent}"})
steps.append({"agent": lead, "instruction": intent})
```

The rule in one line: **anything that writes to a person gets grounded first.**
This is why `CommunicationAgent` can rely on `ctx.prior_outputs["research"]`
existing.

**Step 4 - title.** `_title_from(intent)` normalises whitespace,
capitalises the first letter, and truncates at 70 characters on a word boundary
with `"..."`.

### The model path and `_validate`

```python
PLANNER_SYSTEM = """You are the Executive Planner for a proactive assistant.
Break the user's request into the fewest steps that fully accomplish it.
...
Return JSON: {"title": str, "steps": [{"agent": str, "instruction": str}]}
Rules: 1-5 steps. Research before writing. Never include an "approve" or "send"
step - sending is handled outside the plan."""
```

`_validate(raw)` is the trust boundary. Its docstring: *"Never trust
model-shaped output straight into the executor."* It enforces:

- `raw` must be a `dict` with a non-empty `steps` list, else `None` (keep the
  heuristic plan).
- At most **5** steps (`steps_raw[:5]`).
- Each step's `agent` must be in
  `{"research", "communication", "productivity", "analytics", "automation"}`.
  **`security` and `memory` are not in the allowlist** - a model cannot plan
  its own security review, and cannot schedule a memory write.
- `instruction` must be non-empty, truncated to 600 chars.
- `title` truncated to 200, defaulting to `"Task"`.
- If every step is rejected, the whole plan is rejected.

A model that returns a plan containing `{"agent": "executor"}` gets that step
silently dropped. A model that returns only such steps gets its entire plan
discarded in favour of the keyword plan.

---

## 3. Agent 2 - Research

**File:** `agents/workers.py` · **Capabilities:** signal search, document
retrieval, account history · **Proposes:** no

**Input:** `ctx.step_input["instruction"]`, falling back to `ctx.intent`.
**Output:** `{"findings": [...], "context": [...], "digest": str, "query": str}`

1. `_keywords()` tokenises the query (`[a-z0-9][a-z0-9'-]{2,}`), drops a
   49-word `STOPWORDS` set, keeps the first 14.
2. `_recent_signals()` loads the last **220** `Signal` rows for the user,
   ordered by `occurred_at DESC`, **then removes every signal ingest flagged as
   a hostile injection attempt** (`_is_hostile()` reads
   `raw["hints"]["injection"]["hostile"]`). Its docstring explains why that is
   not redundant with withholding the payload from prompts:

   > A phishing message still carries a plausible subject line and a sender
   > address, and an agent that treats it as ordinary context will happily
   > surface it as a finding and then address a reply to the attacker. Content
   > *and* metadata from a hostile message have to leave the working set
   > together.
3. `_rank()` scores each: `hits * 10 + signal.score * 0.25`, where `hits` is the
   count of query terms appearing anywhere in
   `title + body + actor + connector_key + kind`. Signals with zero hits are
   dropped - **unless** there were no terms at all.
4. Top 8. If nothing matched, it falls back to the 6 highest-scoring signals
   regardless of relevance, so the agent always returns something to work with.
5. Separately loads the 6 highest-`salience` `Memory` rows into
   `output["context"]`.

A `finding` is
`{id, connector, kind, title, actor, body[:400], score, when, flagged}`.
`flagged` is true when the signal carries *any* injection detection - hostile
ones never get this far, but a merely suspicious one is marked so downstream
agents can refuse to reply to it.

The ranking function's docstring names the swap point: *"A keyword index is
enough at laptop scale. In deployment this is the call that goes to the vector
store; the ranking contract stays the same."* The 220-row window is the honest
limit - it is a recency window, not a search over everything.

---

## 4. Agent 3 - Communication

**File:** `agents/workers.py` · **Capabilities:** email drafting, messaging,
follow-ups · **Proposes: yes**

**Output:** `{"draft": {subject, body}, "target": {...}, "channel": str}` plus
one `Proposal` and one `artifact` of type `draft`.

Pipeline:

1. **`_pick_target(instruction, findings)`** - decides who the message is
   actually addressed to, through three filters:

   - **Drop `flagged` findings.** `candidates = [f for f in findings if not
     f.get("flagged")]`. The comment is blunt about the failure it prevents:
     *"Never fall back to a flagged one - that is how a phishing sender becomes
     the recipient."*
   - **Name match**, then any candidate, but only if `_addressable()`.
   - **`_addressable(actor)`** rejects anything without an `@`, anything
     matching the `NO_REPLY` pattern (`billing@`, `no-reply@`,
     `notifications@`, `receipts@`, `support@`, `mailer@`, `postmaster@`,
     `alerts@`, …), and anything on your own domain - *"an invoice reminder has
     your own billing robot as its sender, and replying to that sends a dunning
     letter to yourself."*
   - **Fallback is a placeholder, not a guess.** With no addressable candidate
     it returns the richest unflagged finding with `actor` blanked, so the draft
     renders `[recipient]` and the approval screen shows you that before
     anything goes out.
2. **`_channel_for(target, instruction)`** - the literal word "slack" in the
   instruction forces `("slack", "slack")`. Otherwise the target's connector
   decides: `slack` / `teams` / `discord` stay as chat; anything else becomes
   email on `gmail` or `outlook`, defaulting to `gmail`.
3. **`_draft()`** - with no model, `_template_body()` produces a deterministic
   draft with `[bracketed placeholders]`, branching on invoice / escalation /
   meeting. With a model, `DRAFT_SYSTEM` asks for
   `{"subject": str, "body": str}`; if the parse yields a `body`, it wins
   (subject truncated to 200, body to 4000). Otherwise the template stands.
4. **Proposal** - `action_type` is `post_message` when the channel is slack,
   otherwise `send_email`; `risk=RiskTier.REVIEW`, `reversible=False`.

`DRAFT_SYSTEM` is worth reading in full because it encodes the product's voice
and one hard rule:

> *Reference the specific facts given. Never invent numbers, dates or
> commitments. If a fact is missing, leave a `[bracketed placeholder]` rather
> than guessing.*

The `_template_body` path honours the same rule - every branch leaves explicit
brackets (`[current status]`, `[proposed change]`, `[your name]`) rather than
inventing content. A draft that arrives with brackets in it is working as
designed.

**This is the untrusted-content boundary, and it is framed.** `_facts()`
assembles `title` and `body[:200]` from each finding - strings written by third
parties inside someone else's email or ticket - and passes the whole block
through `injection.wrap_untrusted()` before it reaches a prompt:

```python
def _facts(self, findings: list[dict]) -> str:
    """...Everything here was written by a third party inside someone else's
    email or ticket, so it is framed as untrusted data rather than pasted
    in raw. `wrap_untrusted` also withholds anything that scored as hostile,
    so a payload that survived ingest still never reaches the model."""
    from ..core.injection import wrap_untrusted
    body = "\n".join(...)
    sources = ", ".join(sorted({f["connector"] for f in findings[:6]}))
    return wrap_untrusted(body, source=sources or "connected apps")
```

So there are two screens, not one: ingest-time detection in the sweep (which
flags the signal and writes an audit row), and assembly-time framing here
(which delimits the content, labels its source connectors, and **withholds the
payload entirely** when it scored hostile). `briefing._headline()` wraps its
digest the same way.

If you write a new agent that puts signal content into a prompt, call
`wrap_untrusted()`. Pinned by
`tests/test_safety.py::TestReviewRegressions::test_untrusted_content_is_framed_before_reaching_a_prompt`
and `::test_hostile_finding_is_withheld_from_the_prompt`. See
[docs/06-security.md](06-security.md#7a-c15-prompt-injection-screening).

---

## 5. Agent 4 - Productivity

**File:** `agents/workers.py` · **Capabilities:** calendar management,
scheduling, conflict resolution · **Proposes: yes**

**Output:** `{"conflicts": [...], "upcoming": [...]}` plus up to **3**
`move_event` proposals.

It filters recent signals down to
`{google_calendar, outlook_calendar, zoom, calendly}`, then splits:

- `conflicts` = those with `kind in {"conflict", "prep_needed"}`
- `upcoming` = those with `occurred_at > now`, sorted ascending, first 8

For each of the first 3 conflicts it proposes
`move_event` on `google_calendar` with
`payload={"event_id": <signal.external_id>, "new_time": "[proposed slot]",
"notify_attendees": True}` and `risk=REVIEW`.

Two honest limitations:

- The proposed `new_time` is the literal string `"[proposed slot]"`. There is no
  free/busy lookup; the agent identifies the conflict and proposes the shape of
  the fix, and a human fills the slot.
- `connector_key` is hardcoded to `google_calendar` even when the conflicting
  signal came from `outlook_calendar`. On an Outlook-only account the Security
  agent's scope check will correctly block the proposal, because no
  `google_calendar` connection exists. It fails closed, but it fails.

---

## 6. Agent 5 - Analytics

**File:** `agents/workers.py` · **Capabilities:** dashboards, forecasting,
reporting · **Proposes:** no

**Output:** `{"finance": {...}, "forecast": {...}}` plus one `dashboard`
artifact.

`_finance()` walks the recent signals and aggregates from
`signal.raw["hints"]`:

| Metric | Derived from |
|--------|--------------|
| `overdue_total`, `overdue_count`, `oldest_days` | `kind == "invoice_overdue"`, summing `hints["amount"]`, maxing `hints["days_overdue"]` |
| `at_risk` | `kind in {payment_failed, dispute, deal_risk, revenue_risk}` |
| `escalations` | `kind in {escalation, sla_breach}` |
| `needs_you` | `signal.score >= 60` |

The dashboard artifact has four tiles (with a `tone` of `ok` / `warn` / `bad`)
and two series: signals-by-source as a bar chart, and a cash runway projection
as a line.

### `_runway()` refuses to guess

`_runway(finance, profile)` is not a model forecast, and it is not a demo
constant either. It takes the user's own figures from `User.finance` (set via
`PATCH /api/settings`) and **returns no projection at all when they are
missing**:

```python
starting_cash   = profile.get("starting_cash")
monthly_burn    = profile.get("monthly_burn")
collection_rate = float(profile.get("ar_collection_rate", 0.6))
collected       = finance["overdue_total"] * collection_rate

if not starting_cash or not monthly_burn:
    return {
        "configured": False,
        "series": [],
        "weeks_remaining": None,
        "assumptions": {"ar_collection_rate": collection_rate},
        "collectible_ar": round(collected),
        "note": "No cash projection: set your starting cash and monthly burn "
                "in Settings. I won't guess at a runway figure. "
                f"From observed invoices, ${collected:,.0f} of receivables "
                "looks collectible.",
    }
```

The docstring states why, and it is the most important sentence in the agent:

> An invented starting balance renders identically to a real one, and an
> operator would reasonably act on it. So with no configured cash position this
> returns no projection and says what is missing, rather than producing a
> confident line through made-up numbers. The receivables figure below is real
> - it is summed from observed invoices.

When configured, it projects in 2-week steps out to week 16, returns
`configured: True`, `weeks_remaining`, and `assumptions` carrying all three
inputs so the operator can see which one to argue with. `collectible_ar` is
derived from real observed invoices in both branches.

**Check `forecast.configured` before rendering a runway number.** An unset
`User.finance` is the default state, so an empty series with a `note` is what a
fresh install returns.

---

## 7. Agent 6 - Automation

**File:** `agents/workers.py` · **Capabilities:** workflow execution, record
creation, app integration · **Proposes: yes**

**Output:** `{"connector": str, "action": str, "title": str}` plus one
`Proposal`.

`_route(instruction)` resolves the target app in two passes:

1. **Named app wins.** If the instruction literally contains `jira`, `linear`,
   `asana`, `notion`, `clickup`, `trello`, `hubspot` or `monday`, that app is
   used, with the verb derived from it: `create_task` for asana/clickup,
   `create_page` for notion, `update_deal` for hubspot, `create_issue`
   otherwise.
2. **Noun defaults** (`AutomationAgent.DEFAULTS`):

| Noun in the instruction | Connector | Action |
|-------------------------|-----------|--------|
| `issue` | `linear` | `create_issue` |
| `ticket` | `jira` | `create_issue` |
| `task` | `asana` | `create_task` |
| `page` | `notion` | `create_page` |
| `record` | `hubspot` | `update_deal` |

3. **Fallback:** `("linear", "create_issue")`.

Risk is `RiskTier.SAFE` when the action starts with `create`, else `REVIEW` -
but remember rule 2 of the proposal contract: the catalog overwrites this. For
`linear.create_issue` the catalog also says `_SAFE`, so they agree; for
`hubspot.update_deal` the catalog says `_REVIEW`, which matches too.

---

## 8. Agent 7 - Security

**File:** `agents/security.py` · **Capabilities:** permission verification,
risk analysis, egress control

This is the only agent the orchestrator calls outside the plan, and the only one
that can turn a `Proposal` into something a human will be asked about.

**Input:** `ctx.step_input = {"proposals": [...], "user_directed": bool}`.
**Output:** `{"verdicts": [...], "blocked": [reason, ...]}` with
`result.proposals` set to **only the cleared ones**.

`user_directed` comes from `task.origin == "user"` and nothing else. A proactive
or scheduled task cannot set it.

### `review()` - four checks, in order

**1. Policy floor.** Returns immediately if `action_type` is in:

```python
FORBIDDEN_ACTIONS = {
    "transfer_balance":          "Moving money between accounts is not something I can do.",
    "send_payment":              "Sending payments directly is not something I can do.",
    "trade_securities":          "Trading securities is not something I can do.",
    "change_mfa":                "Changing multi-factor settings has to be done by you.",
    "rotate_root_credentials":   "Rotating root credentials has to be done by you.",
}
```

The blocked reason is the user-facing sentence, which lands verbatim in the
thread message. No consent flag reaches this check.

**1b. Catalog risk.** `risk = catalog.risk_for(connector_key, action_type)`,
overwriting the agent's claim. If the catalog says `FORBIDDEN` (e.g.
`stripe.transfer_balance`, `paypal.send_payment`), blocked here too. This is a
second, independent floor: a connector author can forbid a verb without editing
`FORBIDDEN_ACTIONS`.

**2. Scope** (`_scope_ok`). Fails closed on each of:

- no `Connection` row for `(user, connector_key)` →
  *"X is not connected."*
- `not conn.enabled` or `conn.status != "connected"` →
  *"X is disconnected or disabled."*
- the spec declares `write_scopes`, the connection has a non-empty `scopes`
  list, and the two do not intersect →
  *"X is connected read-only - it has no write scope granted."*

Note the `granted and` guard: an **empty** `scopes` list passes. That is a
seeding convenience (a demo connection with no scopes recorded still works) and
a real deployment should populate `scopes` from the OAuth grant so the check
has teeth.

**3. Egress.** `_renderable(proposal)` collects the strings that would actually
leave the machine - keys `subject`, `body`, `message`, `description`, `text`,
`content` from both `preview` and `payload`, de-duplicated with
`dict.fromkeys`. That text goes to
`redaction.check_egress(content, destination=..., user_directed=...)`.
`destination` is `payload["to"]`, else `payload["channel"]`, else the connector
key.

Checking the rendered content rather than the instruction is the point: the
instruction is what the user asked for, the rendered body is what actually
ships.

**4. User rules and tier.** Loads the user's enabled `PolicyRule` rows and
blocks if any `kind == "never"` rule's `pattern` is a substring of
`action_type`, with the message *"You asked me never to {pattern}."* Then
appends `risk tier: <tier>` and, when `not reversible`, `not reversible once
done` to the reasons, writes `proposal.risk = risk`, and passes.

### What it cannot do

Stated in the module docstring: *"The agent can downgrade nothing. It can only
hold, block, or let an action proceed to the human gate."* There is no branch in
`review()` that returns a lower tier than the catalog's, and no branch that
marks a proposal as not needing approval.

---

## 9. Agent 8 - Memory

**File:** `agents/memory.py` · **Capabilities:** recall, preference learning,
episodic history

`MemoryAgent.run()` dispatches on `ctx.step_input["mode"]`: `"write"` →
`_write()`, anything else → `_recall()`. In practice the orchestrator does not
dispatch to this agent at all; it calls the module-level helpers directly. The
class exists so memory is reachable from a plan if a future planner needs it.

### Module functions

| Function | Called by | Behaviour |
|----------|-----------|-----------|
| `embed(text) -> list[float]` | `remember`, `recall` | 96-dim hashing projection. `blake2b` per token into a bucket, then L2-normalised. Deterministic, offline. |
| `cosine(a, b)` | `recall` | plain dot product (vectors are already normalised) |
| `remember(ctx, content, *, kind, tags, source, salience, key)` | all writers | upserts when `key` is given (keeps the higher `salience`), else inserts |
| `recall(ctx, query, *, limit=6)` | `run_task`, `MemoryAgent` | loads **all** memories for the user, scores `sim*0.6 + salience*0.3 + recency*0.1`, `+0.12` for `kind == "long"`, drops anything below `0.05`, bumps `use_count` and `last_used_at` on the survivors |
| `capture_preference(ctx, utterance)` | `create_task` (user-origin only) | promotes to `kind="long"`, `salience=0.9` **only** if the text is >= 12 chars and matches a `PREFERENCE_MARKER` |
| `record_episode(ctx, title, outcome, tags)` | end of `run_task` | writes `"[YYYY-MM-DD] {title} - {outcome}"` at `kind="episodic"`, `salience=0.4` |
| `behaviour_history(db, user_id)` | `monitor.sweep_user` | `{kind: {"handled": n, "dismissed": n}}` from `Signal.state` |

`recency = 1.0 / (1.0 + age_days / 45.0)` - a 45-day half-ish life.

### Preference capture is deliberately narrow

```python
PREFERENCE_MARKERS = (
    r"\b(?:always|never|from now on|going forward|in future|stop|don'?t ever)\b",
    r"\bi (?:prefer|like|want|need|hate|don'?t want)\b",
    r"\b(?:remember|keep in mind|note) that\b",
    r"\bmy (?:preference|rule|policy) is\b",
)
```

The docstring's reasoning: *"An assistant that over-remembers becomes an
assistant that acts on a throwaway remark from six weeks ago, so only explicit
preference statements are promoted."*

The dedupe key is `"pref:" + blake2b(re.sub(r"\W+", " ", text.lower()))`, so
restating the same preference updates one row instead of accumulating near-
duplicates.

`embed()`'s docstring names the swap point: *"Swap for a real encoder by
changing this one function; nothing else depends on how the vector is
produced."* The cost of the hashing embedding is that it is lexical, not
semantic - "cash runway" and "liquidity horizon" do not match. The benefit is
that it runs with zero dependencies and is reproducible.

---

## 10. Adding a ninth agent

Worked example: a `Compliance` agent that reviews outbound contract language.

**1. Write the class.** Put it in its own file,
`apps/api/duet/agents/compliance.py`:

```python
from .base import Agent, AgentContext, AgentResult, Proposal

class ComplianceAgent(Agent):
    name = "compliance"
    role = "Compliance"
    capabilities = ("contract review", "policy check")

    def run(self, ctx: AgentContext) -> AgentResult:
        instruction = ctx.step_input.get("instruction") or ctx.intent
        drafts = ctx.prior_outputs.get("communication", {}).get("draft", {})
        body = drafts.get("body", "")

        issues = [p for p in POLICY_PATTERNS if p.search(body)]
        if issues:
            return AgentResult(
                ok=True,
                summary=f"{len(issues)} policy issue(s) in the draft",
                output={"issues": [p.pattern for p in issues]},
            )
        return AgentResult(ok=True, summary="Draft is clean", output={"issues": []})
```

Rules to hold to:

- **No instance state.** The registry holds one shared instance.
- **Return, do not raise,** for expected failures - set `ok=False` and `error`.
  Raising works (the orchestrator catches it) but loses your summary.
- **Never import `connectors.registry` or `core.vault`.** If you need to change
  something outside this process, emit a `Proposal`.
- Truncate anything you put in `output`; it is persisted on every step.
- Read `ctx.prior_outputs[<earlier agent>]`, not the database, for
  within-task data flow.

**2. Register it** in `agents/registry.py`:

```python
from .compliance import ComplianceAgent

AGENTS = {..., "compliance": ComplianceAgent()}
ORDER = (..., "compliance")       # position = Agent Health display order
```

**3. Make it reachable.** Two independent switches, and you need both:

- **Heuristic routing** - add to `planner.ROUTES` (as a lead agent) or
  `planner.COMPOSITES` (as a step in a multi-agent plan). Remember that ROUTES
  is first-match-wins and ordered, so inserting high can steal traffic from
  `analytics`.
- **Model routing** - add `"compliance"` to the `allowed` set in
  `planner._validate()` **and** describe it in `PLANNER_SYSTEM`'s specialist
  list. Forgetting `_validate` means the model can name your agent and the step
  is silently dropped; forgetting `PLANNER_SYSTEM` means it never names it.

**4. If it proposes actions,** declare the verbs in
`connectors/catalog.py` as `WriteAction` entries with real risk tiers. An
`action_type` the catalog does not know becomes `SENSITIVE`, which is safe but
means a human sees a scary tier for a harmless operation.

**5. What you do not need to do.** Nothing in `core/orchestrator.py` changes.
Dispatch is `get_agent(planned["agent"])`, the security review runs over
whatever proposals came back, and approvals are created from whatever cleared.
The pipeline is agent-count-agnostic by construction.

### What you cannot add

An agent that executes. The Security agent is wired into the orchestrator by
name (`get_agent("security")` in `_security_review`), and the executor is a
separate module outside the agent system entirely. A ninth agent can observe,
reason, draft and propose. It cannot be given a credential, and it cannot clear
its own proposals.

---

## 11. Answering a question

Not an agent, but the step that decides what a completed task *says*.
`core/orchestrator.py::_compose_answer()`:

> Without this a question like "what's been waiting on me longest?" completed
> successfully and reported "Done: <the question>" - the work happened and the
> user learned nothing. Any task that produces findings but proposes no action
> was a question, and a question deserves prose.

The trigger, at the end of `run_task`:

```python
# A task with nothing to approve was a question. Answer it.
if not approvals and outputs:
    answer = _compose_answer(db, task, user, outputs)
    if answer:
        db.add(Message(role="assistant", kind="text", content=answer,
                       task_id=task.id, meta={"answered": True, ...}))
```

So the shape of the result depends on what the specialists produced:

| Specialists produced | What the user gets |
|----------------------|--------------------|
| proposals | an approval request: *"I've prepared 1 thing… Nothing has been sent or changed yet."* |
| findings, no proposals | **prose answering the question**, `meta={"answered": true}` |
| neither | `"Done: <title>"` |

`ANSWER_SYSTEM` is written to stop the two failure modes a reporting prompt
usually has - burying the answer and padding when it does not have one:

```
- Lead with the answer, not with what you did.
- Be specific: use the actual names, numbers and dates in the findings.
- If the findings do not answer the question, say so plainly rather than padding.
- Never invent anything that is not in the findings.
- Under 120 words. No greeting, no sign-off, no "I hope this helps".
```

Two implementation details that matter:

- **The digest carries each finding's age in days**, via a local `_age()` helper
  that renders `", 12d old"`, `", today"` or `", due in 3d"`. The comment
  explains why: *"Without them the model can rank by priority but cannot answer
  'which has been waiting longest', and it ends up caveating an answer it had
  the data to give."* This pairs directly with `WAITING_ON_YOU` in the priority
  engine - both exist so "how long has this been sitting?" is answerable.
- **The findings go through `wrap_untrusted()`.** Same discipline as
  `CommunicationAgent._facts()`; the answer path is not a loophole.

With no model configured there is a deterministic fallback - `"Here's what I
found for \"<intent>\":"` followed by up to six finding titles with their
connectors, plus any `productivity` or `analytics` summaries. It is also the
fallback when a configured model fails, so the path never produces nothing.
