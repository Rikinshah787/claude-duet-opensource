# 09 - Roadmap

An engineering roadmap, not a pitch. What runs today is stated from the code;
what is missing is stated without hedging.

---

## 1. What ships today

Everything in this section is implemented and runs on `git clone` with no
dependencies beyond Python 3.12.

| Subsystem | File | State |
|-----------|------|-------|
| Priority engine | `core/priority.py` | **complete.** 8 factors, ~35 signal kinds, 12 connector biases, 4 thresholds, quiet-hours handling with midnight wrap, explainable per-factor reasons |
| Proactive sweep | `core/monitor.py` | **complete.** Read-only, per-connection error isolation, DB-level dedupe, one grouped interrupt per sweep |
| Morning brief | `core/briefing.py` | **complete.** 5 deterministic sections + pending approvals + derived recommendations; the model writes only the headline |
| Task orchestration | `core/orchestrator.py` | **complete.** Recall → plan → dispatch → security review → approvals, with per-step persistence and per-agent exception isolation |
| Approval executor | `core/executor.py` | **complete.** The single write chokepoint, with 5 execution-time re-checks, revert support and stale expiry |
| Scheduler | `core/scheduler.py` | **works.** 4 loops in a daemon thread. Non-atomic task claim; no backoff; `WorkflowRun` unused |
| Security agent | `agents/security.py` | **complete.** 4-check gate, forbidden floor, scope check, egress check, user `never` rules |
| Injection defence | `core/injection.py`, `agents/workers.py` | **complete.** 8 weighted patterns, hostile threshold at 60, invisible-character sanitiser. Three enforcement points: screened at ingest (audited), framed at prompt assembly by `CommunicationAgent._facts()` and `briefing._headline()` with hostile payloads withheld, and hostile signals removed from the agents' working set so a phishing sender cannot become a recipient |
| Egress gate | `core/redaction.py` | **complete** for the formats it covers. 6 STRICT detectors (Luhn-validated cards), 3 GUARDED, log scrubber, and no switch to disable it |
| Sandbox | `core/sandbox.py` | **tested library infrastructure, not a wired capability.** subprocess / docker / browser backends, env denylist, traversal rejection, honest capability probe exposed via `GET /api/agents`. **No caller on the task path** |
| Test suite | `apps/api/tests/` | **196 tests passing** across 6 files. Safety invariants, injection defence, sandbox isolation and live-adapter signal shape are the load-bearing parts |
| Telemetry | `core/telemetry.py` | **complete.** Per-agent latency and error rates, noise ratio, approval age, connector health, injection count, cost projection, Prometheus export, optional OTel |
| Credential vault | `core/vault.py` | **complete** as a local store. Fernet, handle-only access, no getter, real deletion. No rotation |
| Audit chain | `core/audit.py` | **complete.** SHA-256 chain over 17 action types, `verify_chain()` reports the first break. Unkeyed, `at` not covered |
| 8 agents | `agents/` | **complete.** Planner (3 composites + 5 routes + model path with validation), 5 workers, security, memory |
| Memory | `agents/memory.py` | **works.** 96-dim hashing embedding, salience + recency ranking, conservative preference capture, behaviour history |
| Connector framework | `connectors/base.py` | **complete.** Read/write split protocol, 4 data types, fail-closed base |
| Connector catalog | `connectors/catalog.py` | **complete.** 58 specs, 16 categories, 92 write actions with risk tiers and notes |
| Live connectors | `connectors/live/`, `registry.py` | **6 of 58 work today**: `github` (via the `gh` CLI, no token), `imap`, `linear`, `notion`, `slack`, `stripe`. The other 52 resolve to `UnavailableConnector` and `connect` refuses with 409. No fake adapter ships |
| Model layer | `llm/client.py`, `llm/agent_cli.py` | **complete and vendor-agnostic.** 5 providers; `auto` resolves to whichever agent CLI you already have signed in (Claude Code, Codex, Gemini, Cursor, OpenCode via one `SPECS` table), else an API key, else Ollama, else the offline planner. Prompt on stdin, throwaway cwd, tool use disabled |
| REST API | `api/` | **complete** for the implemented surface. 38 routes across 4 routers; the 4 demo endpoints are gone |
| Data model | `models.py` | **complete.** 13 tables, 5 enums, meaningful constraints and indexes |
| Packaging | `Dockerfile`, `docker-compose.yml`, `deploy/k8s/`, `Makefile`, `scripts/` | **works.** Two-stage non-root image, 6-service compose, 9 k8s manifests, PowerShell + bash launchers |

| Priority engine ageing | `core/priority.py` | **complete.** `WAITING_ON_YOU` reverses the age term for the 9 kinds where someone is blocked on the user, taking `max(freshness, overdue)` |
| Question answering | `core/orchestrator.py` | **complete.** A task that finds things but proposes no action is answered in prose, with each finding's age, rather than reporting "Done: <the question>" |

The honest headline: **six apps work against real accounts, the other 52 are
catalogued and the product says so, and there is no fake data anywhere in the
shipped package.**

---

## 2. The MVP gap

Seven things stand between this and something a stranger could use on their own
accounts. Ordered by how much they block the others.

### G1 - More connectors, and OAuth for the ones that need it

**Substantially delivered.** Six apps work against real accounts today, which
was the gap this item described. What remains is breadth and the OAuth
infrastructure the harder vendors need.

| Done | Still missing |
|------|---------------|
| 6 live adapters: `github`, `imap`, `linear`, `notion`, `slack`, `stripe` | the other 52 catalogued apps |
| No fake fallback - `UnavailableConnector` returns nothing and `connect` refuses with 409 | — |
| `has_adapter` corrected to mean "a real integration exists", kept identical to `registry.REAL` | — |
| `register()` / `available()` / `live_keys()` / `reset_cache()`, with `_register_live()` wrapped so one bad import disables one app | — |
| 48 tests with the network stubbed, asserting hint units and that forbidden actions make no call | — |
| Two apps that need no OAuth app at all (`github` via the `gh` CLI, `imap` via an app password) | OAuth for Google, Microsoft, Atlassian, HubSpot, Salesforce |
| `POST /api/sync` and `POST /api/connectors/{key}/sync` | webhook ingestion |

**The OAuth work is now the blocker on breadth,** not adapter-writing. Of the
six shipped, none required an OAuth app: two reuse an existing local auth and
four take an API key or integration token. Every remaining high-value
app - Gmail, Google Calendar, Outlook, Drive, Jira - needs a real authorize
flow.

| Piece | Effort |
|-------|--------|
| OAuth authorize/callback routes, `state` binding, encrypted token storage | 1 week |
| Refresh-on-401 inside `vault.inject()`, writing back through `vault.put()` | 3 days |
| Record granted scopes, not requested ones (closes a `_scope_ok` weakness) | 1 day |
| First OAuth adapter (Gmail: list + get + send + modify, with pagination) | 1 week |
| Each subsequent API-key adapter | 2-5 days |
| Vendor-specific rate-limit handling and backoff | 1 week |
| Webhook ingestion for the apps that support it | 1 week |

**A credible MVP is 8-12 connectors, not 58.** With six done, adding Gmail or
Outlook, a calendar, and one of QuickBooks/Xero gets there - roughly **3-4
weeks** including the OAuth infrastructure, most of it spent once on OAuth
rather than per adapter.

### G2 - Authentication

**The gap:** `X-Duet-User` is an unauthenticated header. Any caller can act as
any user that exists. `require_role("owner", "member")` is wired but
untriggerable, because nothing creates a second user and the auto-created
account is `owner`.

Per-user scoping on the read endpoints is now done (`/api/audit`,
`/api/agents`, `/api/metrics`, `/api/memory` all filter on the acting user), so
what remains is proving *who* the acting user is. One exception stays by
necessity: `/api/audit/verify` recomputes the whole chain, because a hash chain
is only verifiable end to end.

**Why it is G2 and not G5:** every multi-user feature is downstream of it, and
it is the difference between "local-first by design" and "insecure".

**Work:** session or token auth in `api/deps.py::current_user` (the one place
identity is resolved, by design), user CRUD, invite flow, per-user scoping on
`/audit` and `/agents`. **2-3 weeks.**

### G3 - Schema migrations

**The gap:** `init_db()` is `Base.metadata.create_all()`. Additive only. There
is no Alembic directory. Changing a column means dropping the database.

**Why it is this high:** it blocks every other item. You cannot ship G1 and then
discover `Connection` needs a `token_expires_at` column without a migration
path, and you cannot ask a user to re-enter credentials for a point release.

**Work:** Alembic, baseline revision from the current models, run as an init
container or Job before the API starts. **3-4 days.** Do it before G1.

### G4 - Durable workflows

**The gap:** `WorkflowRun` exists with `state`, `attempt`, `max_attempts` and an
indexed `next_run_at`, plus `schedule()` and `due_runs()` helpers in
`core/scheduler.py`. **Nothing calls them.** Consequences: no exponential
backoff, no way to express "follow up in 3 days", no resume of a multi-step
workflow, and a task left `RUNNING` by a crash is stranded with no
reconciliation pass.

**Work, staged:**

| Stage | Effort | Delivers |
|-------|--------|----------|
| Fifth scheduler loop over `due_runs()` | 3 days | backoff, deferred work, durable multi-step |
| Startup reconciliation for stranded `RUNNING` tasks | 1 day | crash recovery |
| Atomic task claim (`FOR UPDATE SKIP LOCKED`) | 2 days | **unblocks a second API replica** |
| Idempotency key on `executor.decide()` | 3 days | safe retries; required before any distributed executor |
| Lift `run_task` into Temporal | 2-3 weeks | durable timers, history, horizontal workers |

Do them in that order. Temporal first gives you durable timers and still
duplicate emails. Full reasoning in
[docs/07-workflows.md](07-workflows.md#7-in-process-now-temporal-later).

### G5 - Vector recall (Qdrant)

**The gap:** `agents/memory.py::recall()` loads **every** memory row for the
user and ranks in Python. `embed()` is a 96-dimension `blake2b` hashing
projection - lexical, not semantic. `Memory.embedding` is a JSON column.
`settings.qdrant_url` and `settings.qdrant_collection` exist, a Qdrant
container is in `docker-compose.yml` and `deploy/k8s/qdrant.yaml`, and **no
code reads any of it**.

Concretely: "cash runway" and "liquidity horizon" do not match, and
`ResearchAgent` searches a 220-row recency window rather than history.

**What makes it tractable:** both docstrings name the single swap point. *"Swap
for a real encoder by changing this one function; nothing else depends on how
the vector is produced."* And on ranking: *"In deployment this is the call that
goes to the vector store; the ranking contract stays the same."*

**Work:** an embedding provider behind `embed()`, a Qdrant-backed `recall()`
with the local path as fallback, and a backfill. The settings and the
infrastructure are already there. **1-2 weeks.**

### G6 - Push notifications

**The gap:** `_maybe_interrupt()` writes a `Message` row with
`proactive=True`. That is the entire delivery mechanism. There is no SSE, no
WebSocket, no email digest, no mobile push. The web client has to poll, and a
"notify now" signal is only seen when the user is already looking.

**This is the most product-visible gap on the list.** A proactive assistant whose
interrupt mechanism requires the user to be watching is not proactive.

**Work:** SSE on `/api/thread/stream` (3 days, biggest win per hour), web push
(1 week), email delivery of the brief (3 days), native push (out of scope
without a mobile client). **~2 weeks** for the web-only path.

### G7 - The sandboxed browser VM

**Partly built.** `core/sandbox.py` exists with three backends, and it is the
most complete piece of unused infrastructure in the tree:

| Built | Missing |
|-------|---------|
| `subprocess` backend: per-task workspace, `ENV_DENYLIST` of 14 credential vars, `python -I -S`, timeout, output cap, traversal rejection | **any caller.** No agent imports `sandbox`; no `WriteAction` routes to it |
| `docker` backend: `--network none`, `--read-only`, `--cap-drop ALL`, `no-new-privileges`, memory/cpu/pid caps, tmpfs | per-domain network allowlist for the cases that legitimately need egress |
| `browser` backend: Playwright Chromium, click/fill/wait/screenshot actions, page text scanned by `injection.scan()` and withheld when hostile | credential injection at the form-fill boundary - the `1password.use_login` catalog note describes a flow that does not exist |
| `available()` capability probe, rendered by `GET /api/agents` | a DOM-diff reviewer that turns an observed page change into a `Proposal` rather than acting |
| — | an interaction budget, so a model in a loop cannot click 400 times |
| — | `DUET_SANDBOX_BACKEND` defaults to `subprocess`, which the code itself calls *"not a security boundary"*. A deployment that wires this up must flip it to `docker` |

This is the component that would let the assistant act on apps with no API, and
it remains the highest-risk thing on the roadmap: a browser with a real session,
in a loop, driven by a model reading attacker-controlled page content.

**Work:** ~3-4 weeks for the missing half, and it should still be last. Every
other item makes the system more trustworthy; this one expands the attack
surface. The ordering argument is unchanged by the fact that the plumbing now
exists.

---

## 3. Phases

Effort is engineer-weeks for one experienced engineer. Dependencies are listed
because the ordering is the useful part.

### Phase 1 - Make it extensible (4-5 weeks)

| Item | Effort | Depends on |
|------|--------|------------|
| Alembic migrations (G3) | 0.5 | — |
| SSE for the thread (G6, partial) | 0.5 | — |
| Fifth scheduler loop over `WorkflowRun` (G4, stage 1) | 0.5 | — |
| Startup reconciliation for stranded tasks | 0.2 | G4-1 |
| `PolicyRule` update/toggle endpoint | 0.2 | — |
| Per-connector sweep cadence via `due_for_sweep()` | 0.3 | — |
| Surface `Completion.degraded` in the UI | 0.2 | — |
| Reconcile `apps/web/lib/types.ts` with the backend's actual enum values | 0.2 | — |
| Validate `timezone` in `PATCH /api/settings` instead of silently falling back | 0.1 | — |
| Fix `Briefing.for_date` to the user's local day | 0.2 | — |
| Install `pytest-asyncio` or drop `asyncio_mode` from the config | 0.1 | — |

**The test suite is already here** - 196 tests across `test_safety.py`,
`test_live_connectors.py`, `test_priority.py`, `test_agents.py`,
`test_injection.py` and `test_sandbox.py`, with the safety invariants, the
injection defence, the sandbox isolation boundary and every live adapter's
signal shape all pinned. Writing it before the first adapter was the right
order: the first real integration is also the first chance to break the safety
gate, and `TestApprovalGate` plus `TestIngestPipeline` now fail if anyone
does.
The control-to-test map is in
[docs/06-security.md](06-security.md#10-testing-the-security-model).

What the suite does **not** cover: RBAC (C13), because there is no way to
create a second user; telemetry (C17), which is reporting not enforcement; and
anything behind real OAuth, which does not exist yet.

### Phase 2 - Breadth and multi-user (6-8 weeks)

Slack, Stripe, Linear and Notion are **done** - they needed no OAuth app. What
is left is the apps that do, plus the infrastructure they share.

| Item | Effort | Depends on |
|------|--------|------------|
| OAuth infrastructure (G1) - authorize/callback, `state`, refresh-on-401, granted-scope recording | 2 | Phase 1 (migrations) |
| Gmail adapter | 1 | OAuth |
| Google Calendar adapter | 1 | OAuth |
| Outlook + Outlook Calendar | 1.5 | OAuth |
| QuickBooks or Xero adapter | 1 | OAuth |
| Jira adapter | 0.8 | OAuth |
| Rate-limit handling and per-vendor backoff | 1 | G4-1 |
| Webhook ingestion | 1 | G4-1 |
| Authentication (G2) | 2 | migrations |

### Phase 3 - Make it good (6-8 weeks)

| Item | Effort | Depends on |
|------|--------|------------|
| Qdrant recall + a real embedding model (G5) | 1.5 | — |
| Per-user priority weight learning (replace `historical_factor`) | 2 | signal volume from Phase 2 |
| Atomic task claim + idempotent executor (G4-3, G4-4) | 1 | — |
| Multi-user RBAC with real isolation | 1.5 | G2 |
| Web push + email brief delivery (G6) | 1.5 | G2 |
| Data retention: `Memory.expires_at` reaper, signal archival | 0.5 | — |
| Non-US detectors in the egress classifier (IBAN, NHS, Aadhaar, SIN, NINO) | 0.5 | — |
| An injection false-positive review queue, and threshold tuning from real traffic | 0.5 | Phase 2 volume |

### Phase 4 - Scale and expand (10+ weeks)

| Item | Effort | Depends on |
|------|--------|------------|
| Temporal migration (G4-5) | 3 | G4-3, G4-4 |
| Sandboxed browser VM (G7) | 5 | everything |
| Remaining ~45 connectors | 0.5 each, parallelisable | OAuth |
| Keyed and externally-anchored audit chain | 1 | — |
| Agent dispatch into the sandbox (code execution as a capability) | 1.5 | G7 |

---

## 4. Where production actually breaks

Not "what would be nice to optimise" - where the current code has a cliff.

### Bottleneck 1 - Connector fan-out (the real one)

`scheduler._run_sweep()` is a nested loop over every user and every monitored
connection, **sequentially, on one thread**:

```python
for user in all_users:
    monitor.sweep_user(db, user)      # → for conn in connections: adapter.fetch()
```

With 18 connected apps at ~400ms per vendor round trip (reachable today only in
tests - six adapters exist, so a real user sweeps at most six), one user's sweep takes
roughly 7 seconds. The arithmetic against a 900-second interval:

| Users | Sweep time | Fits in 900 s? |
|-------|-----------|----------------|
| 1 | ~7 s | yes |
| 10 | ~70 s | yes |
| 100 | ~12 min | **no** |
| 1,000 | ~2 hours | no |

So **the practical ceiling is around 100 users per API process**, and it is a
latency ceiling, not a CPU one - the thread is almost entirely blocked on
network I/O.

Worse, the sweep shares the thread with `_run_queued_tasks()`. A long sweep
delays every queued task, so the user-facing symptom of too many users is not
"stale signals", it is "the assistant stopped responding to me".

**Fixes, in order of value per unit of work:**

1. **Async or threaded fan-out within a sweep.** The adapters are I/O-bound and
   independent. `httpx.AsyncClient` with a per-user `asyncio.gather` turns 7
   seconds into ~1. Biggest single win, and it needs no new infrastructure.
2. **Separate the sweep from the task runner.** Different threads at minimum;
   different processes once G4-3 makes the claim atomic.
3. **Webhooks** (G1). Inverts the cost curve - polling costs the same whether
   anything happened or not.
4. **Per-connector cadence.** `due_for_sweep()` already exists, unused. A
   calendar does not need the same interval as a payroll system.
5. **Shard by user.** Only correct after the atomic claim.

### Bottleneck 2 - Memory recall

```python
rows = list(ctx.db.execute(select(Memory).where(Memory.user_id == ctx.user.id)).scalars())
qv = embed(query)
for mem in rows:
    sim = cosine(qv, mem.embedding or [])
```

Every memory row for the user, loaded into Python, on **every task**. With 96
floats per row serialised as JSON:

| Memories | Rows loaded | Approximate latency |
|----------|-------------|---------------------|
| 100 | 100 | negligible |
| 10,000 | 10,000 | ~200 ms + JSON parsing |
| 100,000 | 100,000 | seconds |

`ix_memory_lookup(user_id, kind, salience)` does not help, because cosine
similarity is not an index seek. `recall()` also **writes** on every call
(`use_count += 1`, `last_used_at = now` for each hit), so a read path takes a
write lock - which on SQLite with the scheduler thread also writing is exactly
the contention the 5-second `busy_timeout` is papering over.

`ResearchAgent._rank()` has the same shape over a 220-signal window - bounded,
so it does not grow, but it means research is a recency search rather than a
history search.

**Fix:** G5. The swap point is one function.

### Bottleneck 3 - Model throughput

The two model-backed call sites are `PlannerAgent` and
`CommunicationAgent._draft()`, plus the briefing headline. All are
**synchronous and blocking**, on the same thread as the sweep and the task
runner.

A 7B model on CPU takes 20-60 seconds per call. `DUET_LLM_TIMEOUT_S` is 120.
So **one slow model call stalls every queued task and delays the sweep for up
to two minutes.** `max_parallel_tasks = 4` does not help - it is a batch size,
and the batch runs sequentially.

| Hardware | Plan latency | Tasks/minute |
|----------|-------------|--------------|
| CPU, 7B | 20-60 s | 1-3 |
| Consumer GPU, 7B | 2-5 s | 12-30 |
| Hosted API | 1-3 s | 20-60 |

**Fixes:** move model calls off the scheduler thread; batch independent agent
calls within a plan; keep the heuristic planner for the cases it already
handles (it is not a fallback, it is the right answer for the common shapes);
cache plans for repeated intents.

### Bottleneck 4 - SQLite write contention

Two writers on one file: the API request handlers and the scheduler thread.
WAL mode plus `busy_timeout=5000` makes this survivable, and `db.py`'s comment
says why it is there: *"Without this the scheduler thread and the API thread
fight over the same SQLite file."*

The cliff is a single long write transaction - a sweep inserting 200 signals -
blocking a request handler for up to 5 seconds, after which it raises
`database is locked`. Postgres removes this entirely, which is why Compose and
k8s use it.

### Bottleneck 5 - The audit chain

`verify_chain()` is O(n) over the whole table with no checkpointing. At a
million rows that is a minute of CPU, and `record()` does a
`SELECT … ORDER BY id DESC LIMIT 1` before every insert - fine on an indexed
primary key, but it serialises audit writes.

**Fix:** periodic checkpoints (verify from the last known-good anchor), and a
cached head hash in memory for `record()`.

---

## 5. Cost model

The question that decides the sweep cadence. Numbers are from the code, not from
a vendor price sheet.

The running system computes a version of this itself:
`core/telemetry.py::_cost()` reports `tokens_in`, `tokens_out`,
`estimated_spend_usd`, `sweeps_per_day_at_current_cadence` and
`token_cost_driver` at `GET /api/metrics`, using
`COST_PER_MTOK_IN = 0.08` / `COST_PER_MTOK_OUT = 0.24` - *"Electricity and
depreciation, not an API bill."* Use that for your own install; the analysis
below is for choosing the cadence before you have data.

There is deliberately **no per-sweep token figure**, and the code explains why:

> Deliberately not "tokens per sweep". Sweeping and scoring use no model turns
> at all - every token above was spent running a task. Dividing task spend by
> sweep count would invent a per-sweep cost that does not exist and would make
> lengthening the interval look like a saving.

### What costs a model turn today

| Call site | When | Turns |
|-----------|------|-------|
| `PlannerAgent.run()` | once per task, **only if** `llm.provider != "heuristic"` | 1 |
| `CommunicationAgent._draft()` | once per communication step | 1 |
| `briefing._headline()` | once per brief, **only if** provider is not heuristic | 1 |

**The sweep itself costs zero model turns.** `monitor.sweep_user` →
`priority.score_signal` is pure arithmetic. The only downstream effect is
indirect: more signals surfaced means more briefs with content, and more tasks
the user then chooses to start.

So for anyone tuning `DUET_SWEEP_INTERVAL_S`: halving the interval doubles the
vendor calls (and the rate-limit risk) and does **not** double the model cost.
`config.py`, `monitor.py` and `telemetry.py` all now say this; an earlier
revision of each claimed sweeps cost model turns, which was wrong.

### Daily turns for one active user

Assume ~30 signals/day across the connected apps, 5 user-initiated tasks, and
the briefing on.

| Item | Turns/day |
|------|-----------|
| Sweeps (96/day at 900 s) | **0** |
| Briefing headline (1/day) | 1 |
| Planner, 5 user tasks | 5 |
| Communication drafts, ~3 of those tasks | 3 |
| Tasks started from a brief recommendation or a signal, ~2 | 2 + 1 draft |
| **Total** | **~12** |

### Tokens per turn

Estimated from the prompts in the code:

| Turn | Prompt | Output |
|------|--------|--------|
| Planner | `PLANNER_SYSTEM` (~130 tok) + up to 8 recall lines + the intent | ~80 tok JSON |
| Draft | `DRAFT_SYSTEM` (~110 tok) + recipient + thread + up to 6 facts at ~250 chars | ~160 tok JSON |
| Brief headline | `BRIEF_SYSTEM` (~70 tok) + up to 12 digest lines + 4 approvals | capped at 140 tok |

Call it ~600 in / ~130 out per turn on average: **~7k input and ~1.5k output
tokens per user per day.**

### What that means

**Local inference (Ollama, 7B).** Marginal cost is electricity. The constraint
is *latency*, not money - see Bottleneck 3. At ~12 turns/day one user is
trivial; the ceiling is concurrent users against one GPU.

**Hosted API, small model.** At roughly $0.15/M input and $0.60/M output:
~$0.002 per user per day, **~$0.06/user/month**. Model inference is not the cost
driver at this volume.

**Hosted API, frontier model.** At roughly $3/M input and $15/M output:
~$0.04/user/day, **~$1.20/user/month.** Still not the dominant cost.

**Where the money actually goes:** connector API calls and the egress they
imply. 18 apps × 96 sweeps/day = **1,728 vendor requests per user per day**, or
~52,000/month. Most vendor APIs are free within a quota, so the real costs are
rate-limit headroom and the engineering time to handle 429s per vendor.

### Cadence sensitivity

| `DUET_SWEEP_INTERVAL_S` | Sweeps/day | Vendor calls/day (18 apps) | Model turns/day |
|------------------------|-----------|---------------------------|-----------------|
| 60 | 1,440 | 25,920 | ~12 |
| 300 | 288 | 5,184 | ~12 |
| **900 (default)** | **96** | **1,728** | **~12** |
| 1,800 | 48 | 864 | ~12 |
| 3,600 | 24 | 432 | ~12 |

The model column is flat. That is the real argument for the default: going from
900 s to 60 s is a **15x increase in vendor calls** for latency the user cannot
perceive (see [docs/07-workflows.md](07-workflows.md#4-why-the-sweep-cadence-is-minutes-to-hours)),
and the right answer for the genuinely urgent kinds - `sla_breach`, `incident`,
P1 tickets - is webhooks, not a shorter poll.

### If cost becomes a problem

1. **Keep the heuristic planner for the cases it handles.** The three composites
   and five routes cover the common shapes exactly; spending a model turn to
   rediscover that `"draft a reply to Dana"` means research-then-communication
   is waste. This is already the design.
2. **Webhooks over polling** for the apps that support them.
3. **Cache plans** keyed on a normalised intent.
4. **Two-tier models**: small for planning and classification, large for drafts
   a human will read.
5. **Raise, do not lower, `sweep_interval_s`** - but for vendor rate limits,
   not model spend. `config.py` is now explicit: *"Raise this rather than lower
   it, but for the right reason: scoring is pure arithmetic, so a sweep costs no
   model turns. What it costs is one API call per connected app per sweep …
   Model spend scales with tasks you run, not with how often it looks."*

---

## 6. Things to fix that are not features

Small, cheap, and each one removes a lie from the codebase.

| # | Issue | Where |
|---|-------|-------|
| 1 | `ConnectorSpec.docs` is declared and never populated or surfaced | `connectors/catalog.py` |
| 2 | `TaskStep.input` and `TaskStep.tool` are declared and never written | `models.py`, `orchestrator.py` |
| 3 | `AgentResult.memories` is declared and ignored by the orchestrator | `agents/base.py` |
| 4 | `Task.parent_id` is declared and never written - no subtask support | `models.py` |
| 5 | `MemoryKind.SHORT` and `.SEMANTIC` are declared and never written | `models.py` |
| 6 | `TaskStatus.BLOCKED` is declared and never assigned | `models.py` |
| 7 | `ProductivityAgent` hardcodes `google_calendar` even for Outlook-sourced conflicts | `agents/workers.py` |
| 8 | `security._scope_ok()` passes an empty `scopes` list | `agents/security.py` |
| 9 | `PATCH /api/settings` does not validate `timezone`; a bad value silently falls back to UTC | `api/apps.py`, `monitor.py` |
| 10 | `Briefing.for_date` is UTC while the trigger hour is user-local | `briefing.py`, `scheduler.py` |
| 11 | `monitor.due_for_sweep()` is defined and never called | `core/monitor.py` |
| 12 | `monitor.sweep_all()` is defined and never called (the scheduler has its own loop) | `core/monitor.py` |
| 13 | `scheduler.schedule()` / `due_runs()` are defined and never called; `WorkflowRun` has no writer | `core/scheduler.py` |
| 14 | `settings.redis_url` is read nowhere | `config.py` |
| 15 | `apps/web/lib/types.ts` declares disposition and step-status literals that the backend never emits | `apps/web/lib/types.ts` |
| 16 | `PostThreadResponse` in the web types omits `reply`, which the backend always sends | `apps/web/lib/types.ts` |
| 17 | `Completion.degraded` is set and never surfaced to a user | `llm/client.py` |
| 18 | `core/sandbox.py` has no caller on the task path - tested library infrastructure, not a wired capability | `core/sandbox.py` |
| 19 | `settings.qdrant_url` and `qdrant_collection` are defined and read nowhere | `config.py` |
| 20 | **`.env.example` is stale and misleading**: `DUET_LLM_PROVIDER=heuristic` and `DUET_LLM_MODEL=qwen2.5:7b-instruct` contradict the code defaults (`auto`, empty), and `DUET_LLM_CLI` / `DUET_QDRANT_COLLECTION` / `DUET_SANDBOX_BACKEND` are missing while `DUET_REDACT_SENSITIVE` is still listed and ignored | `.env.example` |
| 21 | `GET /api/metrics/prometheus` is user-scoped, which is wrong for a cluster scrape | `api/system.py` |
| 22 | `telemetry.COST_PER_MTOK_IN/OUT` are module constants, not configuration | `core/telemetry.py` |
| 23 | `pytest-asyncio` is in the `dev` extra but absent from `.venv`, so the suite warns on `asyncio_mode` | `pyproject.toml` |

Items 9, 16, 17, 19 and especially **21** are the ones where the code's claim
and its behaviour still differ. Those should go first, because someone will read
the file and believe it - and copying `.env.example` verbatim today pins you to
the offline planner even with a usable agent CLI signed in.

### Closed since the first review

Each is now pinned by a test rather than by a comment, so it cannot silently
regress:

| Was | Now | Pinned by |
|-----|-----|-----------|
| `injection.wrap_untrusted()` defined and never called | Called by `CommunicationAgent._facts()` and `briefing._headline()`; hostile payloads withheld from the prompt | `TestReviewRegressions::test_untrusted_content_is_framed_before_reaching_a_prompt`, `::test_hostile_finding_is_withheld_from_the_prompt` |
| Hostile signals still reachable as findings, so a reply could be addressed to the attacker | `_recent_signals()` drops hostile rows; `_pick_target()` refuses `flagged` candidates and non-addressable senders | `TestHostileSignalsLeaveTheWorkingSet` (4 tests) |
| `settings.redact_sensitive` read nowhere | Removed from `Settings`; redaction unconditional and `config.py` says so | `::test_there_is_no_switch_to_disable_redaction` |
| `auto_approve` rule accepted and ignored | Removed from `NewRule`'s pattern; returns `422` | `::test_auto_approve_rule_is_not_accepted` |
| `reversible` taken from the proposing agent | Taken from the catalog, like `risk` | `::test_reversibility_comes_from_the_catalog_not_the_agent` |
| `GET /api/audit` unscoped | Scoped to the acting user plus `user_id IS NULL` system rows | `::test_audit_is_scoped_to_the_acting_user` |
| `GET /api/agents` aggregated `task_steps` globally | Scoped via `TaskStep.task_id IN (the user's tasks)` | — |
| `AnalyticsAgent._runway()` hardcoded `starting_cash` / `monthly_burn` | Reads `User.finance`; returns `configured: false` and no series when unset | — |
| `catalog.py` docstring pointed at a non-existent `adapters.py` | Points at `registry.py` and states that an app without an adapter returns nothing | — |
| "sweeps cost model turns" | Corrected in `config.py`, `monitor.py` and `telemetry.py`; `avg_tokens_per_sweep` replaced with `token_cost_driver: "tasks"` | — |
| `apps/api/tests/` empty | 196 tests across 6 files | itself |
| `demo/reset` deleted `TaskStep` rows globally | `admin/reset` scopes them to the user's tasks and reports destroyed credentials | — |
| All 58 apps resolved to a scripted `DemoConnector` | 6 real adapters; the rest resolve to `UnavailableConnector` and `connect` refuses with 409 | `test_live_connectors.py` (48 tests) |
| `has_adapter` meant "an adapter is intended" (20 entries, none real) | means "a real integration exists", corrected on 15 entries, kept identical to `registry.REAL` | `TestRegistration` |
| The model layer was Claude-specific | vendor-agnostic: 5 agent CLIs behind one `SPECS` table, plus anthropic / ollama / openai / heuristic | — |
| `DUET_FORCE_DEMO_CONNECTORS` could make the product serve fake data | removed; the scenario is a test fixture that is not importable from `duet/` | `conftest.py` autouse fixture |
| Age always decayed urgency, so 28 live GitHub notifications all scored `ignore` | `WAITING_ON_YOU` reverses it for 9 kinds, taking `max(freshness, overdue)` | `test_priority.py` |
| A question completed with "Done: \<the question\>" | `_compose_answer()` answers in prose, with each finding's age | — |

---

## 7. What not to build

Stating these keeps the roadmap honest about scope.

- **A second model provider abstraction.** Three providers behind one interface
  is already the right amount. OpenAI-compatible covers vLLM, LM Studio,
  llama.cpp, LocalAI and most hosted APIs.
- **A plugin system for agents.** Adding one is three edits (class, registry,
  planner routing) - see [docs/03-agents.md](03-agents.md#10-adding-a-ninth-agent).
  A plugin loader would add indirection and a new trust boundary for no gain.
- **Auto-approval tiers.** `RiskTier.SAFE` exists and still requires a human.
  That is approval fatigue, and it is the right default while there is no
  authentication: an auto-approve path plus an unauthenticated header is a
  remote-action primitive. Revisit after G2.
- **A learned priority ranker, yet.** `historical_factor()` is the documented
  slot, and its docstring says why the counting version is there: *"deliberately
  simple so its behaviour stays predictable."* An explainable weighted sum you
  can tune beats an opaque model you cannot, until there is enough per-user
  signal to validate against.
- **Streaming model output.** The two model call sites both produce JSON that is
  parsed, not read. Streaming a parsed artifact buys nothing.
