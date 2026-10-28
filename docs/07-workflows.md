# 07 - The autonomous workflow engine

`apps/api/duet/core/scheduler.py`. One daemon thread, four loops, started from
`main.py`'s lifespan hook.

This is what makes the product proactive. Without it the system is a
request/response assistant that happens to have a connector layer.

---

## 1. Shape

```python
class Scheduler:
    def start(self) -> None   # idempotent; spawns a daemon thread named "duet-scheduler"
    def stop(self, timeout=5.0) -> None
    @property
    def running(self) -> bool
    def status(self) -> dict  # surfaced by GET /api/health

scheduler = Scheduler()       # module-level singleton
```

Lifecycle, in `main.py`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if os.environ.get("DUET_DISABLE_SCHEDULER", "").lower() not in {"1", "true", "yes"}:
        scheduler.start()
    else:
        log.info("scheduler disabled by DUET_DISABLE_SCHEDULER")
    yield
    scheduler.stop()
```

`DUET_DISABLE_SCHEDULER` exists because *"an autonomous background loop is
surprising"* in tests and CLI use. Set it for any pytest run that touches the
app factory - otherwise your test database grows signals while you assert on
it.

The thread is a **daemon** thread, so it never blocks interpreter exit; `stop()`
sets an `Event` and joins with a 5-second timeout. A sweep already in flight at
shutdown is abandoned mid-pass, which is safe because every connection commits
individually.

### The tick

```python
def _loop(self) -> None:
    while not self._stop.is_set():
        try:
            self._tick()
        except Exception as exc:          # the loop must never die
            self.stats["errors"] += 1
            self.stats["last_error"] = f"{type(exc).__name__}: {exc}"
            log.exception("scheduler tick failed")
        self._stop.wait(TASK_POLL_S)       # 2.0s
```

Two things to notice. The catch-all is deliberate and commented - a crashed
scheduler is a silently non-proactive product, which is worse than a logged
error. And the wait is on the stop `Event`, not `time.sleep()`, so shutdown is
immediate rather than up to 2 seconds late.

`_tick()` runs four pieces of work on three different cadences:

```python
def _tick(self) -> None:
    self._run_queued_tasks()                                    # every 2s
    if now - self._last_sweep >= settings.sweep_interval_s:      # every 900s
        self._run_sweep()
    if now - self._last_housekeeping >= HOUSEKEEPING_EVERY_S:    # every 300s
        self._housekeeping()
    self._maybe_brief()                                          # every 2s, guarded
```

| Loop | Cadence | Constant |
|------|---------|----------|
| task runner | 2 s | `TASK_POLL_S = 2.0` |
| sweep | 900 s | `settings.sweep_interval_s` (`DUET_SWEEP_INTERVAL_S`) |
| housekeeping | 300 s | `HOUSEKEEPING_EVERY_S = 300.0` |
| briefing | checked every 2 s, fires at most once per user per day | `settings.briefing_hour` |

Timing uses `time.monotonic()`, so a system clock change or a DST shift cannot
make a loop fire early or stall. The briefing loop is the exception - it has to
use wall-clock local time, because "7am for the user" is a wall-clock concept.

---

## 2. The four loops

### Task runner - the hot loop

```python
def _run_queued_tasks(self) -> None:
    with session_scope() as db:
        queued = select(Task).where(status == QUEUED) \
                             .order_by(Task.created_at.asc()) \
                             .limit(settings.max_parallel_tasks)
        ids = [t.id for t in queued]

    for task_id in ids:
        if self._stop.is_set(): return
        with session_scope() as db:
            task = db.get(Task, task_id)
            # Another worker may have claimed it between the two sessions.
            if task is None or task.status != TaskStatus.QUEUED.value:
                continue
            run_task(db, task)
            self.stats["tasks_run"] += 1
```

FIFO by `created_at`. The **two-session pattern** is the important part: ids are
collected in one transaction and each task is re-read and re-checked in its own.
That gives a per-task transaction boundary - a task that throws does not roll
back its siblings - and it is where a second worker's claim would be noticed.

Three honest notes:

- **`max_parallel_tasks` is a batch size, not a concurrency level.** The tasks
  in the batch run **sequentially** in this thread. A slow task blocks the
  others, and a task that blocks on a 120-second LLM timeout
  (`DUET_LLM_TIMEOUT_S`) stalls the queue for that long.
- **The claim is not atomic.** Re-reading the status narrows the window but does
  not close it. Two API processes against one database can both run the same
  task. The fix is a `SELECT … FOR UPDATE SKIP LOCKED` claim on Postgres, or an
  `UPDATE … SET status='running' WHERE status='queued'` with a rowcount check -
  neither of which SQLite supports well, which is why it is not there.
- **`run_task` has no cancellation check.** `POST /tasks/{id}/cancel` sets the
  status, which stops a task that has not started; a running one finishes.

### Sweep

```python
def _run_sweep(self) -> None:
    with session_scope() as db:
        for user in db.execute(select(User)).scalars():
            if self._stop.is_set(): return
            monitor.sweep_user(db, user)
        self.stats["sweeps"] += 1
```

Every user, every monitored connection, every 900 seconds. Note it does **not**
use `monitor.due_for_sweep(conn, interval_s)`, which exists as a
per-connection staleness helper and is currently unreferenced - the obvious
improvement is per-connector cadence (a calendar does not need the same interval
as a payroll system), and the helper is the hook for it.

All users are swept inside **one** `session_scope()`, so the commit at the end
covers every user. `sweep_user` commits per connection internally, so a failure
partway through still leaves the earlier connections' signals persisted.

### Briefing

```python
def _maybe_brief(self) -> None:
    for user in users_with_briefing_optin:
        if monitor._local_hour(user) < settings.briefing_hour:
            continue
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if briefing_exists(user, for_date=today):
            continue
        briefing_mod.generate(db, user)
```

The idempotency guard is **the existence of a `Briefing` row for today's
`for_date`**, not a flag. There is no "already sent" boolean to get out of sync
with reality; if the row is there, the brief happened.

Two consequences worth knowing:

- **`for_date` is UTC**, while the hour comparison is user-local. For a user in
  `America/Los_Angeles`, 7am local on the 26th is 14:00 UTC on the 26th, so
  they line up. For a user at UTC+13, 7am local on the 27th is 18:00 UTC on the
  **26th**, and that day's brief is filed under the 26th. Single-user
  local-first makes this invisible; a global deployment needs `for_date`
  computed in the user's zone.
- **A restart does not re-send.** The row survives, so the check still holds.

`generate()` looks back 24 hours (`lookback_h=24`) regardless of when it last
ran, so a missed day produces one brief covering the most recent 24 hours, not a
48-hour catch-up.

### Housekeeping

```python
def _housekeeping(self) -> None:
    expired = executor.expire_stale(db)        # pending approvals past expires_at
    stuck = select(Task).where(status == FAILED, attempts < settings.task_retry_limit)
    for task in stuck:
        task.status = TaskStatus.QUEUED.value
        task.error = None
```

Two jobs, every 300 seconds. Approval expiry is a safety control
(see [docs/06-security.md](06-security.md#7-c8-c14-the-remaining-controls)); the retry sweep is the second
half of the retry mechanism described below.

---

## 3. Retries

Retries live in **two** places, and understanding the interaction matters.

**Inside `run_task`** (`core/orchestrator.py`), on an unhandled exception:

```python
task.status = (TaskStatus.QUEUED.value
               if task.attempts < settings.task_retry_limit
               else TaskStatus.FAILED.value)
task.error = f"{type(exc).__name__}: {exc}"[:2000]
```

`attempts` is incremented at the **top** of `run_task`, so with
`task_retry_limit = 3` a task runs at most 3 times. A re-queued task is picked
up by the next task-runner pass - within ~2 seconds.

**In housekeeping**, every 300 seconds, any `FAILED` task whose
`attempts < task_retry_limit` is set back to `QUEUED` with `error` cleared.

Note that the two paths cannot both be reached for the same task under the
current limit: `run_task` only writes `FAILED` once `attempts >= limit`, which
is exactly the condition housekeeping excludes. The housekeeping retry is a
recovery net for a task marked `FAILED` by some other path (a manual database
edit, or a future failure mode), not the normal retry loop.

### No backoff

A re-queued task retries after ~2 seconds, three times, with no delay growth.
For a transient database lock that is right. For a rate-limited vendor API it is
wrong - three attempts in six seconds against a 429 is worse than one. The
`WorkflowRun` table carries `attempt`, `max_attempts` and `next_run_at`
precisely so that backoff becomes expressible; nothing uses them yet.

### Partial failure inside a task

A step-level exception does **not** fail the task.
`orchestrator._execute_plan()` catches per agent:

```python
try:
    result = agent.run(ctx)
except Exception as exc:    # one bad specialist must not kill the task
    result = AgentResult(ok=False, summary=f"{agent_name} failed",
                         error=f"{type(exc).__name__}: {exc}")
```

The step is recorded as `error`, its outputs are discarded, and the plan
continues. A task with a failed research step still runs its communication
step - with no research in `prior_outputs`, which is why every agent must handle
a missing predecessor. `CommunicationAgent` does: `_pick_target` returns `{}`
and `_template_body` produces a bracketed placeholder draft.

### What is durable across a restart

| State | Survives? | Why |
|-------|-----------|-----|
| A `QUEUED` task | yes | picked up on the next tick |
| A task that was `RUNNING` when the process died | **no** - it is stranded | nothing re-queues a `RUNNING` task. `attempts` is already incremented, so a manual re-queue still respects the limit |
| `TaskStep` rows for a partially-run task | yes | `_execute_plan` commits per step, so the trace is readable |
| A `PENDING` approval | yes | and will be expired by housekeeping once past `expires_at` |
| An `APPROVED` approval whose execution was interrupted | **ambiguous** | status stays `approved`; nothing retries it. Whether the vendor call landed is unknown |
| Connector cursors | yes | `connections.cursor` is committed per connection per sweep |
| Signals | yes | committed per connection; `uq_conn_external` makes the next sweep's re-fetch a no-op |

The stranded-`RUNNING` case is the one real durability gap. A startup
reconciliation pass - "re-queue any `RUNNING` task with no step finished in the
last N minutes" - is the missing piece, and it is the kind of thing Temporal
gives you for free.

---

## 4. Why the sweep cadence is minutes-to-hours

`settings.sweep_interval_s` defaults to **900 seconds**. The config comment is
precise about what that buys, and about what a sweep does *not* cost:

> Raise this rather than lower it, but for the right reason: scoring is pure
> arithmetic, so a sweep costs no model turns. What it costs is one API call per
> connected app per sweep - at 900s that is ~96 sweeps/day against every
> vendor's rate limit, and 60s would be 1440. Model spend scales with tasks you
> run, not with how often it looks.

And `monitor.py`:

> **Checking is not free.** Scoring itself is arithmetic and costs no model
> turns, but every sweep is one API call per connected app. With 18 apps at a
> 900s interval that is ~1,700 vendor calls a day; at 60s it would be ~26,000.
> The interval is minutes-to-hours for rate limits and politeness, not because
> the maths is expensive.

Four reasons, in order of how much they actually constrain the design:

**1. Vendor rate limits and politeness.** A sweep over 18 connected apps is 18+
API calls. At 900s that is ~1,700 calls/day; at 60s, ~26,000. Gmail, Slack and
Stripe all meter per-user requests, and the failure mode when you exceed them is
`conn.status = "error"` - the assistant goes blind rather than slow. This is the
binding constraint.

**2. Not model cost.** Worth stating because it is the obvious wrong
assumption: `monitor.sweep_user` → `priority.score_signal` is pure arithmetic.
Halving the interval doubles vendor calls and does **not** change model spend,
which scales with tasks. `telemetry._cost()` reports
`token_cost_driver: "tasks"` for exactly this reason and deliberately omits a
per-sweep token figure. Full arithmetic in
[docs/09-roadmap.md](09-roadmap.md#5-cost-model).

**3. Human latency is the real bound.** The sweep's output is a notification or
a brief. A person does not act on an overdue invoice in under a minute. The
latency that matters is end-to-end: notice → score → decide → surface → human
reads → human decides. Shaving the first step from 15 minutes to 30 seconds
changes nothing a user can perceive, because the last step is measured in
minutes to hours.

**4. Interruption is the scarce resource, not information.**
`MAX_INTERRUPTS_PER_SWEEP = 1` caps proactive messages to one per sweep per
user. Sweeping more often would mostly raise the interrupt rate, and the
`monitor.py` docstring names that as the failure mode: *"A burst of
notifications is how a proactive assistant teaches someone to ignore it."*

### When minutes is genuinely wrong

For a `sla_breach`, a production `incident` or a P1 ticket, 15 minutes is too
slow and the answer is not a shorter interval - it is **webhooks**. Which brings
us to:

---

## 5. Event-driven triggers

**Status: not implemented.** There is no webhook endpoint, no signature
verification, no inbound event route. The sweep is the only ingestion path.

That is the single biggest correctness-and-cost improvement available, because
polling and events have inverted cost curves: polling costs the same whether
anything happened or not, while events cost nothing when nothing happens.

The pieces already in place to make it a small change:

- `Connector.fetch()` is cursor-based, so a webhook that says "something changed
  in Gmail" can trigger a targeted `fetch()` for one connection rather than a
  full sweep.
- `uq_conn_external` makes double delivery - a webhook *and* the next scheduled
  sweep both reporting the same event - a no-op at the database level. Webhooks
  can therefore be treated as a latency optimisation over a correct polling
  baseline, not as a thing that has to be exactly-once.
- `monitor._sweep_connection(db, user, conn, …)` already takes a single
  connection.

The shape it should take:

```
POST /api/webhooks/{connector_key}
  ├─ verify the vendor signature (Stripe-Signature, X-Hub-Signature-256, …)
  ├─ resolve Connection by the vendor's account identifier
  ├─ schedule(db, user_id, f"fetch:{connector_key}", state={"hint": payload_id})
  └─ 204 immediately  ← never do work in the request; vendors retry on timeout
```

The important design rule: **a webhook must carry a hint, not data.** Trusting
the payload's contents means the event body becomes an injection surface with no
read-back against the vendor's own API. Trusting only "connection X changed"
keeps the authoritative read inside `fetch()`, where the credential and the
`hints` discipline live.

Keep the scheduled sweep as the floor. Webhooks are lossy - missed deliveries,
expired subscriptions, apps with no webhook support at all - and a poll every 15
minutes is the thing that makes a dropped event a latency problem instead of a
silent one.

---

## 6. `WorkflowRun`: durable state that is not yet used

The table is defined in `models.py` and two helpers exist in `scheduler.py`:

```python
def schedule(db, user_id, workflow_key, *, state=None, delay_s=0) -> WorkflowRun:
    run = WorkflowRun(user_id=user_id, workflow_key=workflow_key,
                      state=state or {}, status="scheduled",
                      next_run_at=datetime.now(timezone.utc) + timedelta(seconds=delay_s))

def due_runs(db, limit=20) -> list[WorkflowRun]:
    return select(WorkflowRun).where(status == "scheduled",
                                     next_run_at <= now) \
                              .order_by(next_run_at.asc()).limit(limit)
```

**Nothing calls either of them.** The scheduler's four loops drive off `Task`,
`Connection`, `Briefing` and `Approval` directly. The module comment is honest
about the intent:

> Durable workflow helpers - the seam Temporal would replace.

The columns that are there and unused tell you what it is for:

| Column | Purpose |
|--------|---------|
| `workflow_key` | which workflow this is a run of |
| `state` | the workflow's own checkpoint - resume data, not just inputs |
| `attempt` / `max_attempts` | per-run retry budget, independent of `Task.attempts` |
| `next_run_at` | **indexed** - the scheduler's poll query, and where backoff lives |
| `status` | `scheduled` → running → done/failed |
| `last_error` | why the last attempt failed |

Three things it would unlock immediately:

1. **Exponential backoff** - `next_run_at = now + 2**attempt * base`.
2. **Deferred work** - "follow up on this invoice in 3 days if it is still
   unpaid" is `schedule(..., delay_s=259200)` and nothing else. Today there is
   no way to express a delayed action at all.
3. **Multi-step workflows that survive a restart** - `state` holds the
   checkpoint, so step 4 of 7 resumes rather than restarting.

Adding a fifth loop (`_run_due_workflows`) that polls `due_runs()` and
dispatches on `workflow_key` is a contained change. It should come **before**
Temporal, because it is the thing that proves the data model is right.

---

## 7. In-process now, Temporal later

> In production these become Temporal workflows: durable timers, retries and
> history for free. The interface here - `WorkflowRun` rows holding `state` and
> `next_run_at` - is shaped so that swap is mechanical. […] Running in-process
> (a daemon thread) is a deliberate local-first choice: no broker to install for
> a laptop deployment.

### The trade-off, stated

**In-process daemon thread**

- Zero dependencies. `git clone`, `uvicorn`, done. No broker, no worker process,
  no `docker compose up` required to see the product behave proactively.
- One process to reason about. The scheduler shares the SQLAlchemy engine and
  the WAL-mode SQLite file with the API.
- No distributed failure modes, no clock skew between components, no queue to
  go stale.

Costs:

- **Single point of failure.** API down = nothing proactive happens. No
  catch-up on restart beyond what the `QUEUED` status implies.
- **Horizontal scaling breaks it.** Two API replicas = two schedulers = two
  sweeps per interval and a non-atomic task claim. See
  [docs/08-deployment.md](08-deployment.md).
- **No durable timers.** "In 3 days" cannot be expressed.
- **Retries are coarse** - a fixed count at a fixed 2-second interval.
- **No execution history** beyond `TaskStep` rows and the audit log.

For a laptop-scale, single-user, local-first product, that trade is clearly
right. The threshold where it stops being right is the moment you want a second
API replica or a workflow that spans days.

### What changes in a Temporal migration

| Concern | Today | With Temporal |
|---------|-------|---------------|
| Scheduling | `_tick()` comparing `time.monotonic()` | a Schedule, or `workflow.sleep()` inside a long-lived workflow |
| Task execution | `run_task(db, task)` on the scheduler thread | a Workflow whose steps are Activities |
| Durable state | `Task.status` + `TaskStep` rows | workflow event history (the `WorkflowRun` table becomes redundant, or a projection for the UI) |
| Retries | `attempts < task_retry_limit`, no backoff | per-Activity `RetryPolicy` with backoff and non-retryable error types |
| Timers | not expressible | `workflow.sleep(timedelta(days=3))` |
| Horizontal scale | breaks | add workers; Temporal handles the dispatch |
| Idempotency | re-read-and-check, not atomic | workflow id deduplication |
| Visibility | `/api/agents`, `/api/audit` | plus the Temporal Web UI |

### What does **not** change

This is the part that justifies the current shape:

- **`core/orchestrator.py::run_task`** - it takes `(db, task)` and is already a
  pure function of persisted state. It becomes a Workflow body with no
  restructuring.
- **Every agent.** `Agent.run(ctx) -> AgentResult` is synchronous, stateless and
  side-effect-free except through the database. Each becomes an Activity as-is.
- **`core/monitor.py::sweep_user`** - one Activity per user, or per connection
  if you want per-connector cadence.
- **`core/briefing.py::generate`** - one Activity on a daily Schedule.
- **`core/executor.py`** - unchanged, and *must* stay the single chokepoint. The
  one thing to get right is that `decide()` is not idempotent by itself; it
  relies on the `status != "pending"` check. As a Temporal Activity it needs an
  idempotency key so a worker retry after a successful-but-unacknowledged vendor
  call does not send twice. That is the single most important correctness
  question in the migration.
- **The data model.** Every table stays. `WorkflowRun` is the only one that
  becomes optional.
- **The security model.** Agents still only propose, the Security agent still
  clears, the executor still acts. Distribution does not change the chokepoint.

### Migration order

1. **Add the fifth loop** over `due_runs()`, in-process. Proves `WorkflowRun`'s
   shape and delivers backoff and deferred work with no new infrastructure.
2. **Add webhook ingestion** that calls `schedule(...)`. Biggest latency and
   cost win, and it needs step 1.
3. **Make the task claim atomic** - `FOR UPDATE SKIP LOCKED` on Postgres. This
   is what unblocks a second API replica, and it is worth doing even if you
   never adopt Temporal.
4. **Make `executor.decide()` idempotent** with an explicit key. Required for
   any retrying executor, Temporal or not.
5. **Only then** lift `run_task` into a Workflow. By that point the hard
   problems - idempotency, claiming, backoff - are already solved, and Temporal
   is replacing the loop rather than the design.

Doing step 5 first is the mistake: you get durable timers and still send
duplicate emails.
