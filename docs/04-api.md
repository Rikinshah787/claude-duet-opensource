# 04 - REST API

Base URL: `http://127.0.0.1:8787`. Every route below is mounted under `/api`
(`main.py` includes each router with `prefix="/api"`).

Source of truth: `apps/api/duet/main.py` and `apps/api/duet/api/`.
Interactive docs are served at `/docs` when `DUET_DEBUG=true`; the OpenAPI
document is at `/openapi.json`.

| Router | File | Routes |
|--------|------|--------|
| `thread` | `api/thread.py` | `/thread`, `/briefing/*` |
| `work` | `api/work.py` | `/tasks/*`, `/approvals/*`, `/signals/*` |
| `apps` | `api/apps.py` | `/connectors/*`, `/settings/*` |
| `system` | `api/system.py` | `/health`, `/me`, `/agents`, `/dashboards/*`, `/metrics*`, `/audit/*`, `/memory`, `/sync`, `/admin/reset` |

38 routes in total. The four `/api/demo/*` endpoints are gone - all return `404`.

---

## 1. Identity, auth and RBAC

There is **no login**. `api/deps.py` resolves identity on every request:

```python
def current_user(db, x_duet_user: str | None = Header(None)) -> User:
    if x_duet_user:                                  # select a specific account
        ... or 404 "No such user: <email>"
    return get_or_create_owner(db)                   # first account by created_at,
                                                     # created on demand as owner@local
```

- **No header** → the owner account. On a fresh database it is created:
  `email="owner@local"`, `name="Owner"`, `timezone="UTC"`, `role="owner"`.
- **`X-Duet-User: <email>`** → that account, or `404`.

The docstring is explicit that this is the single seam: *"adding real auth later
is a change here and nowhere else - every route already asks for the user rather
than assuming one."*

**RBAC.** Two dependency aliases distinguish readers from actors:

| Alias | Dependency | Allowed roles |
|-------|-----------|---------------|
| `UserDep` | `current_user` | any |
| `ActorDep` | `require_role("owner", "member")` | `owner`, `member` |

Every mutating route takes `ActorDep`. A `viewer` gets:

```
403  {"detail": "Your role (viewer) can't perform this action."}
```

Since the auto-created account is `owner`, this only bites once you create
additional users - which has no endpoint yet. The guard is wired and
untriggerable in the default single-user setup.

**No rate limiting and no CSRF protection.** CORS is restricted to
`settings.cors_list` (default `http://localhost:3000,http://127.0.0.1:3000`)
with `allow_credentials=True`. Binding to `127.0.0.1` is what keeps this honest
locally; exposing the port without an auth layer in front is not safe.

---

## 2. Conventions

**Status codes actually produced:**

| Code | When |
|------|------|
| `200` | success (the default for every route except `POST /tasks` and `POST /settings/rules`) |
| `201` | `POST /api/tasks`, `POST /api/settings/rules` |
| `403` | RBAC: a `viewer` hitting an `ActorDep` route |
| `404` | unknown id, or an id belonging to another user, or an unknown connector key |
| `409` | `ExecutionRefused` from the executor; cancelling a finished task |
| `422` | Pydantic validation failure (FastAPI's default shape) |
| `500` | caught by `main.py`'s handler → `{"detail": "<TypeName>. Check the server log."}` |

**Ownership checks** are `if row is None or row.user_id != user.id: 404`. A
resource belonging to someone else is indistinguishable from one that does not
exist.

**Envelopes.** Every response is a JSON object with a named key
(`{"tasks": [...]}`, `{"task": {...}}`), never a bare array. Adding a sibling
field is then a non-breaking change.

**Timestamps** are ISO 8601 strings from `.isoformat()`, UTC, or `null`.

**Limits are clamped server-side**, e.g. `limit=9999` on `/api/signals` becomes
`min(limit, 400)`.

---

## 3. Thread

### `GET /api/thread`

The conversation, oldest first.

| Query | Type | Default | Notes |
|-------|------|---------|-------|
| `limit` | int | `120` | clamped to 500. The newest `limit` messages, then reversed |

```json
{
  "messages": [{
    "id": "a3f...", "role": "assistant", "kind": "brief",
    "content": "Invoice #2291 - Castle Retail - $18,400 - 41 days overdue. …",
    "task_id": null, "approval_id": null, "signal_id": null,
    "proactive": true,
    "meta": {"briefing_id": "…", "sections": ["Money", "Customers"], "item_count": 14},
    "created_at": "2026-10-26T07:00:04.112000+00:00"
  }]
}
```

`role` ∈ `user` / `assistant` / `system`. `kind` ∈ `text` / `brief` /
`approval` / `task_update`. `proactive: true` means the assistant spoke first.

### `POST /api/thread`

Send a message. Requires `ActorDep`.

```json
{ "text": "chase the overdue invoices" }
```

`text`: 1-8000 chars (`422` outside that).

The route triages before spawning work:

1. **`CONVERSATIONAL`** - the whole message is a greeting or acknowledgement
   (`hi`, `hey`, `hello`, `thanks`, `ok`, `got it`, `cool`, `yes`, `no`,
   `what's up`, …). Answered inline. *"Spinning up a task for 'hi' would be both
   slow and silly."*
2. **`STATUS_QUERY`** - `what's the status`, `catch me up`, `anything new`,
   `what do I need`, … Answered from stored state (pending approvals, urgent
   signals, running tasks) **without re-running a sweep**.
3. Otherwise → `create_task(origin="user")` and a `"On it - <title>"`
   acknowledgement. The scheduler picks the task up within ~2 seconds
   (`TASK_POLL_S`).

Response, all three cases:

```json
{
  "message": { "...the user's message..." },
  "reply":   { "...the assistant's immediate reply..." },
  "task":    { "id": "7c1…", "title": "Chase the overdue invoices", "status": "queued" }
}
```

`task` is `null` for cases 1 and 2.

In case 3 the `"On it"` acknowledgement is not the answer - it is a receipt.
**A task that finds things but proposes no action is treated as a question and
answered in prose**, posted to the thread when the task completes with
`meta={"answered": true}`. So `"what has been waiting on me longest?"` returns a
queued task here and a real answer a few seconds later, rather than
`"Done: what has been waiting on me longest?"`. Poll `GET /api/thread`, or
`GET /api/tasks/{id}` for the trace. See
[docs/03-agents.md](03-agents.md#11-answering-a-question).

> The web client's `PostThreadResponse` type in `apps/web/lib/types.ts` declares
> `{message, task}` and omits `reply`. The backend always sends `reply`.

### `GET /api/briefing/latest`

```json
{"briefing": {"id": "…", "for_date": "2026-10-26", "headline": "…",
              "sections": [...], "model": "heuristic",
              "created_at": "…", "delivered_at": "…"}}
```

`{"briefing": null}` when none exists. Section and item shapes are in
[docs/02-data-model.md](02-data-model.md#briefings).

### `POST /api/briefing/generate`

Generate one now from the last 24 hours. `ActorDep`. Returns
`{"briefing": {...}}`.

Side effects: promotes every included `Signal` from `new` to `surfaced`, posts a
`kind="brief"` message, and records `briefing.generated` in the audit chain.
Calling it twice in a day creates two rows - the once-per-day guard lives in the
scheduler, not here.

### `GET /api/briefing/{briefing_id}`

`{"briefing": {...}}`, or `404` if it is not yours.

---

## 4. Tasks

### `GET /api/tasks`

| Query | Type | Default | Notes |
|-------|------|---------|-------|
| `status` | str | — | exact match on a `TaskStatus` value |
| `limit` | int | `50` | clamped to 200 |

```json
{"tasks": [{
  "id": "7c1…", "title": "Chase the overdue invoices",
  "intent": "chase the overdue invoices",
  "status": "awaiting_approval", "origin": "user",
  "lead_agent": "research",
  "plan": [{"agent": "research", "instruction": "Identify every overdue invoice, …"},
           {"agent": "communication", "instruction": "Draft a reminder for each one, …"}],
  "artifacts": [{"type": "draft", "title": "Re: Invoice #2291 …", "body": "Hi Castle, …"}],
  "error": null, "attempts": 1,
  "created_at": "…", "started_at": "…", "finished_at": null
}]}
```

Newest first.

### `POST /api/tasks` → `201`

`ActorDep`.

```json
{ "intent": "prepare me for the Harbor Ventures partner meeting",
  "title": "Harbor prep" }
```

`intent`: 1-8000 chars. `title` optional (defaults to the intent, truncated to
500).

Returns `{"task": {...}}` with `status: "queued"`. Execution is asynchronous.

### `GET /api/tasks/{task_id}`

The full trace.

```json
{
  "task": { ... },
  "steps": [
    {"id": "…", "idx": 0, "agent": "planner", "tool": null,
     "summary": "Planned 2 step(s) for: Chase the overdue invoices",
     "status": "ok", "output": {"plan": [...], "title": "…", "planner": "heuristic"},
     "error": null, "duration_ms": 3, "started_at": "…", "finished_at": "…"},
    {"idx": 1, "agent": "research",  "summary": "…", "status": "ok", ...},
    {"idx": 2, "agent": "communication", "summary": "…", "status": "ok", ...},
    {"idx": 90, "agent": "security",
     "summary": "1 action(s) cleared for your approval.",
     "output": {"verdicts": [{"action": "send_email", "connector": "gmail",
                              "allowed": true, "risk": "review",
                              "reasons": ["connected app has the required scope",
                                          "no sensitive content in the payload",
                                          "risk tier: review",
                                          "not reversible once done"],
                              "blocked_reason": null}],
                "blocked": []}, ...}
  ],
  "approvals": [ { ...full approval objects... } ]
}
```

`idx` ordering: `0` planner, `1..N` plan steps, `90` security. `status` is
`pending` / `running` / `ok` / `error`. `output` has already been through
`redaction.redact()`.

### `POST /api/tasks/{task_id}/cancel`

`ActorDep`. Sets `status: "cancelled"`.

- `404` - unknown or not yours
- `409 {"detail": "That task already finished."}` - status is `done` or
  `failed`

**This is a status flag, not an interrupt.** A task already executing in the
scheduler thread runs to completion; `run_task` does not check for cancellation
between steps. The cancel takes effect for a task still sitting in `queued`
(`_run_queued_tasks` re-reads the status before running it).

---

## 5. Approvals

The human gate. These three routes are the only path to the executor.

### `GET /api/approvals`

| Query | Type | Default | Notes |
|-------|------|---------|-------|
| `status` | str | — | an `ApprovalStatus` value; use `pending` for the inbox |
| `limit` | int | `50` | clamped to 200 |

```json
{"approvals": [{
  "id": "b91…", "task_id": "7c1…",
  "title": "Send to priya@northwind.io: Re: Northwind - we're seeing the sync failures again",
  "rationale": "You asked to chase the overdue invoices. Drafted from 6 item(s) in your connected apps.",
  "action_type": "send_email", "connector_key": "gmail",
  "risk_tier": "review",
  "preview": {"channel": "email", "to": "priya@northwind.io",
              "subject": "Re: …", "body": "Hi Priya,\n\n…",
              "in_reply_to": "Northwind - we're seeing the sync failures again"},
  "payload": {"to": "priya@northwind.io", "subject": "Re: …",
              "body": "…", "channel": "priya@northwind.io"},
  "status": "pending", "reversible": false,
  "decision_note": null, "execution_result": {},
  "created_at": "…", "decided_at": null, "executed_at": null,
  "expires_at": "2026-10-26T19:12:00+00:00"
}]}
```

Render `preview` to the human. `payload` is what runs.

### `POST /api/approvals/{approval_id}/approve`

`ActorDep`. Body optional:

```json
{ "note": "fine, but soften the second paragraph" }
```

`note`: max 1000 chars. It is recorded on the row; **it does not modify the
payload.** There is no endpoint to edit an approval before approving it - you
reject and re-ask.

This runs `executor.decide(approved=True)`, which records the decision **and
executes immediately** in the same request. Response:

```json
{
  "approval": { "...status now 'executed'...",
                "executed_at": "…",
                "execution_result": {"ok": true,
                                     "summary": "Email sent to priya@northwind.io",
                                     "data": {"ref": "gmail-418233", "simulated": true, ...},
                                     "external_id": "gmail-418233"} },
  "result": {"ok": true, "summary": "Email sent to priya@northwind.io", "data": {...}}
}
```

`409` when the approval is not `pending`:
`{"detail": "This action was already executed."}`

When `_preflight` refuses (forbidden verb, catalog-forbidden, connector
disconnected or disabled, or expired), you still get `200` - with
`approval.status == "rejected"` and `result.ok == false` carrying the reason.
Check `result.ok`, not just the HTTP status.

For the six live adapters this is a real vendor call. For an app with no
integration the executor refuses in preflight, so `result.error` is
`"no_adapter"` and nothing was attempted.

### `POST /api/approvals/{approval_id}/reject`

`ActorDep`, same optional `{"note": "..."}`. Status → `rejected`, a thread
message says *"Rejected. Nothing was sent or changed."*, and the parent task is
settled. Returns `{"approval": {...}}`. `409` if not `pending`.

### `POST /api/approvals/{approval_id}/revert`

`ActorDep`, no body. Undo an executed action.

```json
{"approval": {"...status now 'reverted'..."},
 "result": {"ok": true, "summary": "gmail.undo_post_message completed"}}
```

`409` in two cases:

- not executed → `"Only an executed action can be reverted."`
- not reversible, or no revert plan →
  `"send_email can't be undone from here - it has to be handled in gmail directly."`

A revert plan only exists for actions whose adapter returned one -
`notion.delete_page` ships one that un-archives the page, for instance, because
deleting in Notion means archiving.

---

## 6. Signals

### `GET /api/signals`

| Query | Type | Default | Notes |
|-------|------|---------|-------|
| `disposition` | str | — | `notify_now` / `briefing` / `defer` / `ignore` |
| `state` | str | — | `new` / `surfaced` / `handled` / `dismissed` |
| `limit` | int | `100` | clamped to 400 |

Ordered by `score DESC`.

```json
{"signals": [{
  "id": "f02…",
  "title": "Invoice #2291 - Castle Retail - $18,400 - 41 days overdue",
  "body": "Terms were net 30. No payment received, two reminders sent.",
  "actor": "", "connector_key": "quickbooks", "kind": "invoice_overdue",
  "score": 71.0,
  "disposition": "briefing", "state": "new",
  "reasons": ["invoice overdue from quickbooks", "41 days overdue",
              "$18,400 at stake", "has legal or privacy weight",
              "you boosted 'invoice'"],
  "factors": {"kind": 27.6, "urgency": 16.4, "financial": 13.0,
              "relationship": 0.0, "importance": 8.0, "noise": 0.0,
              "preference": 18.0, "history": 0.0},
  "url": null,
  "occurred_at": "2026-10-26T04:12:00+00:00"
}]}
```

`factors` and `reasons` are the explainability contract: the score is the
clamped sum of `factors`, and `reasons` are the sentences behind the non-zero
ones. Render them when the user asks "why am I seeing this?".

> `apps/web/lib/types.ts` declares `SignalDisposition` as
> `'notify' | 'brief' | 'digest' | 'ignore' | 'suppressed'`. The backend values
> are `notify_now` / `briefing` / `defer` / `ignore`. The TS union is `Open<>`
> so it compiles either way, but matching on `'brief'` will never fire.

### `GET /api/signals/{signal_id}`

`{"signal": {...}}` or `404`.

### `POST /api/signals/{signal_id}/dismiss`

`ActorDep`. `state` → `dismissed`. Returns `{"ok": true, "signal": {...}}`.

This is training data. Three or more dismissals of a kind, with a
handled-ratio at or below 0.25, make `priority.historical_factor` subtract 14
points from the next signal of that kind.

### `POST /api/signals/{signal_id}/handle`

`ActorDep`. `state` → `handled`. Pushes the ratio the other way: at or above
0.70 handled, `+10` points.

### `POST /api/signals/{signal_id}/act`

`ActorDep`. Turn an observation into work.

Creates a task with `intent = "Handle this: {title}\n\n{body}"`,
`title = "Handle: {title}"`, `signal_id` set, `origin="user"`, and marks the
signal `handled`. Returns `{"task": {...}}`.

Note `origin="user"` even though the signal came from the proactive path. That
is correct and load-bearing: you clicked, so `user_directed=True` in the egress
check, which relaxes the GUARDED tier for this one piece of work.

---

## 7. Connectors

### `GET /api/connectors`

The gallery: all 58 catalog entries joined against this user's connections and
signal counts, in one call.

```json
{
  "connectors": [{
    "key": "stripe", "name": "Stripe", "category": "finance",
    "color": "#635BFF", "glyph": "S",
    "summary": "Failed payments, disputes, and revenue movement.",
    "auth": "oauth2", "tags": ["payments", "revenue"],
    "signal_kinds": ["payment_failed", "dispute", "invoice_overdue", "revenue_risk"],
    "read_scopes": ["rak_charge_read", "rak_invoice_read"],
    "write_scopes": ["rak_invoice_write", "rak_refund_write"],
    "writes": [
      {"key": "draft_invoice", "label": "Draft an invoice", "risk": "safe",
       "reversible": true, "note": ""},
      {"key": "send_invoice", "label": "Send an invoice", "risk": "review",
       "reversible": false, "note": ""},
      {"key": "issue_refund", "label": "Issue a refund", "risk": "sensitive",
       "reversible": false, "note": "Moves money out. Always requires explicit approval."},
      {"key": "transfer_balance", "label": "Transfer between accounts",
       "risk": "forbidden", "reversible": false,
       "note": "Blocked by policy, even with approval."}
    ],
    "monitored": true,
    "has_adapter": true,
    "live": true,
    "available": true,
    "connected": true,
    "connection": {"id": "…", "status": "connected", "enabled": true,
                   "monitored": true, "account_label": "Stripe (local)",
                   "scopes": ["rak_charge_read", "rak_invoice_read",
                              "rak_invoice_write", "rak_refund_write"],
                   "last_sync_at": "…", "last_error": null},
    "signal_count": 2
  }],
  "categories": ["communication", "calendar", "productivity", "crm", "project",
                 "finance", "commerce", "hr", "storage", "development",
                 "design", "marketing", "support", "security", "automation", "data"]
}
```

Three booleans that answer almost the same question, and the one to render is
`available`:

| Field | Source | Means |
|-------|--------|-------|
| `available` | `registry.available(key)` - `key in REAL and key in BY_KEY` | **can be connected and used today.** This is the field a UI gates on |
| `live` | `registry.is_live(key)` - `key in REAL` | a real adapter is registered |
| `has_adapter` | the catalog's `ConnectorSpec` | a real integration exists |

All three are currently identical and true for exactly six apps - `github`,
`imap`, `linear`, `notion`, `slack`, `stripe` - and false for the other 52.
`available` is the narrowest claim (it also requires the key to be in the
catalog), so gate on it; `has_adapter` is static metadata that could in
principle drift from the registry, which is why
`test_live_connectors.py::TestRegistration` checks they agree.

For the 52 unavailable apps, offer them as "not yet" rather than as connectable:
`POST /connectors/{key}/connect` will return `409`.

### `POST /api/connectors/{key}/connect`

`ActorDep`.

```json
{ "account_label": "work gmail",
  "grant_write": true,
  "credential": {"access_token": "…", "refresh_token": "…"} }
```

All three fields optional. `grant_write` defaults to `true`.

- Scopes granted = `spec.read_scopes` + (`spec.write_scopes` if `grant_write`).
  `grant_write: false` produces a read-only connection, and the Security
  agent's scope check will then block every write proposal for that app.
- `credential` goes straight to `vault.put(user.id, key, credential)` and only
  the returned `handle.ref` is stored on the row. Omitting it stores
  `{"type": "demo", "note": "local placeholder"}`, which exercises the vault
  round-trip but will not authenticate against a real vendor. GitHub is the
  exception: it needs no credential here at all, because the adapter shells out
  to your `gh` CLI login.
- Idempotent: an existing row is reactivated (status `connected`, `enabled`,
  new scopes, new credential ref, `last_error` cleared) rather than duplicated.
  `uq_user_connector` backs that up.
- `404` for an unknown key.
- **`409` for a catalogued app with no integration** - checked before anything
  is written, via `registry.available(key)`:

  ```json
  {"detail": "Salesforce is in the catalog but has no integration yet.
              Connecting it would look like it works and never produce anything."}
  ```

  That second sentence is the reason the guard exists. A connected-but-empty app
  is indistinguishable from a quiet one, so the product refuses rather than
  letting a user believe they are being watched over.
- Records `connector.connected` in the audit chain.

```json
{"connection": {"connector_key": "github", "id": "…", "status": "connected", ...}}
```

**This is not OAuth.** There is no authorize redirect, no token exchange, no
refresh, and `Connection.scopes` records what was *requested* rather than what a
provider granted. Two of the six live apps sidestep that: `github` reuses your
`gh` CLI login and `imap` takes an app password. See
[docs/05-connectors.md](05-connectors.md).

### `POST /api/connectors/{key}/disconnect`

`ActorDep`. *"Disconnecting destroys the credential, not just the row."*

`vault.forget(credential_ref)` removes the secret from `vault.enc`, then the row
is set to `status="revoked"`, `enabled=false`, `credential_ref=None`,
`scopes=[]`. Audit: `connector.disconnected` with
`detail={"credential_destroyed": true}`.

`{"ok": true, "connector_key": "gmail"}`, or `404` if not connected.

Signals already collected **survive** - `signals.connection_id` is
`ON DELETE SET NULL` and the row is not deleted anyway.

### `PATCH /api/connectors/{key}`

`ActorDep`.

```json
{ "monitored": false, "enabled": true }
```

Both optional; `null`/absent leaves the field alone. `monitored: false` drops
the app out of the sweep while keeping the connection. Returns
`{"connection": {...}}`, `404` if not connected.

---

## 8. Settings

### `GET /api/settings`

```json
{
  "quiet_hours": {"start": 21, "end": 7},
  "briefing_hour": 7,
  "briefing_optin": true,
  "timezone": "UTC",
  "sweep_interval_s": 900,
  "finance": {},
  "rules": [{"id": "…", "kind": "vip", "pattern": "harborventures.com",
             "weight": 1.0, "note": "Lead investor on the Series A",
             "enabled": true}]
}
```

`quiet_hours`, `timezone`, `briefing_optin` and `finance` are per-user columns.
`briefing_hour` and `sweep_interval_s` are **process-wide config**
(`settings.briefing_hour`, `settings.sweep_interval_s`) and are read-only here -
they come from `DUET_*` env vars, so the UI must not present them as editable.

### `PATCH /api/settings`

`ActorDep`.

```json
{ "quiet_hours": {"start": 22, "end": 6},
  "timezone": "America/New_York",
  "briefing_optin": true,
  "finance": {"starting_cash": 420000, "monthly_burn": 68000,
              "ar_collection_rate": 0.6} }
```

All fields optional; absent means unchanged. Hours are coerced with `% 24`.
`timezone` is truncated to 64 chars and **not validated** - an invalid name makes
`monitor._local_hour()` fall back to UTC via its bare `except`, silently.

`finance` is allowlisted to `starting_cash`, `monthly_burn` and
`ar_collection_rate`, each coerced to `float`; unknown keys and `null` values
are dropped. These are the figures `AnalyticsAgent._runway()` uses, and until
they are set the executive dashboard returns **no runway projection** rather
than a fabricated one. See `GET /api/dashboards/executive` below.

Returns `{"ok": true, "quiet_hours": {...}, "timezone": "...",
"finance": {...}}`.

### `POST /api/settings/rules` → `201`

`ActorDep`.

```json
{ "kind": "vip", "pattern": "dana@harborventures.com",
  "weight": 1.5, "note": "Series A partner" }
```

- `kind` must match `^(vip|mute|boost|never)$`, else `422`. `auto_approve` is
  **deliberately not accepted** - there is no auto-approve path in the code, and
  a setting that silently does nothing is worse than no setting.
- `pattern`: 1-300 chars, matched as a **lowercased substring**, not a regex.
- `weight` clamped to `[0.0, 3.0]`.

Effects, per kind, are tabulated in
[docs/02-data-model.md](02-data-model.md#policy_rules). All four kinds have a
consumer.

### `DELETE /api/settings/rules/{rule_id}`

`ActorDep`. `{"ok": true}`, or `404`.

---

## 9. System and observability

### `GET /api/health`

No auth, no user.

```json
{
  "status": "ok",
  "version": "0.1.0",
  "db": true,
  "llm": {"ok": true, "provider": "agent_cli",
          "detail": "Claude Code (/home/you/.local/bin/claude)"},
  "scheduler": {"running": true, "started_at": "…", "sweep_interval_s": 900,
                "next_sweep_in_s": 412, "briefing_hour": 7,
                "tasks_run": 3, "sweeps": 2, "briefings": 1,
                "errors": 0, "last_error": null}
}
```

`llm.provider` is the **resolved** provider, not the configured one. With the
default `DUET_LLM_PROVIDER=auto` it is whatever this machine turned out to
have - see [section 12](#12-the-model-layer-at-runtime).

`status` is `"ok"` or `"degraded"` (database unreachable). Used by the Docker
`HEALTHCHECK` and by `scripts/dev.sh` / `dev.ps1` as the readiness gate.

`llm.ok` is `true` for the heuristic provider by definition. Every other
provider reports a distinct, actionable `detail`:

| Provider | `detail` on success | `detail` on failure |
|----------|--------------------|---------------------|
| `agent_cli` | `"Claude Code (/path/to/claude)"` | `"codex CLI not found (installed: Claude Code)"`, or `"no agent CLI found (tried: claude, codex, gemini, cursor, opencode)"` |
| `anthropic` | — | `"anthropic provider needs DUET_LLM_API_KEY"` |
| `ollama` | `"ollama: qwen2.5:7b-instruct"` | `"ollama is up but 'qwen2.5:7b-instruct' is not pulled"` or `"ollama unreachable: ConnectError"` |
| `heuristic` | `"heuristic (no model server)"` | — |

Asking for a CLI you do not have is reported rather than silently substituted,
which is the behaviour to surface in a UI.

### `GET /api/me`

```json
{"id": "…", "email": "owner@local", "name": "Owner", "timezone": "UTC",
 "role": "owner", "quiet_hours": {"start": 21, "end": 7},
 "briefing_optin": true,
 "counts": {"pending_approvals": 2, "unread_signals": 9, "urgent_signals": 1,
            "running_tasks": 0, "connections": 18, "memories": 7}}
```

`unread_signals` counts `state == "new"`; `urgent_signals` counts
`disposition == "notify_now"` with state in `{new, surfaced}`.

### `GET /api/agents`

Agent definitions joined with observed behaviour from `task_steps`.

```json
{"agents": [{
   "name": "planner", "role": "Executive Planner",
   "capabilities": ["goal decomposition", "prioritisation", "action planning"],
   "runs": 3, "errors": 0, "avg_ms": 4, "last_run": "…", "status": "ok"
 }],
 "llm": {"ok": true, "detail": "heuristic (no model server)", "provider": "heuristic"},
 "sandbox": {"subprocess": true, "docker": true, "browser": false,
             "active": "subprocess",
             "note": "subprocess is process isolation, not a security boundary - use the docker backend for untrusted code"}}
```

`status`: `"error"` when every run failed, `"ok"` when there is at least one
run, `"idle"` with none.

`sandbox` is `core/sandbox.py::Sandbox.available()` - what this machine can
actually run, probed at request time (`shutil.which("docker")` and an
`import playwright`). Surface `browser: false` rather than assuming the
capability: `browse()` returns a `SandboxResult` telling you to
`pip install playwright && playwright install chromium`.

The agent aggregation is **scoped to the acting user**: each query is restricted
to `TaskStep.task_id IN (SELECT id FROM tasks WHERE user_id = ?)`, since
`task_steps` has no `user_id` column of its own. `GET /api/metrics` does the
same aggregation through `telemetry.collect(db, user_id=user.id)`.

### `GET /api/metrics`

`core/telemetry.py::collect(db, user_id=user.id)` - agent health, autonomy
health, and what the unattended work is costing, in one query pass. Scoped to
the calling user.

```json
{
  "generated_at": "…",
  "agents": [{"agent": "research", "runs": 4, "errors": 0, "error_rate": 0.0,
              "avg_ms": 12, "max_ms": 31}],
  "tasks":   {"by_status": {"done": 2, "awaiting_approval": 1}, "stuck": 0, "in_flight": 0},
  "signals": {"by_disposition": {"briefing": 14, "ignore": 9, "notify_now": 3, "defer": 2},
              "last_24h": 28, "noise_ratio": 0.393},
  "approvals": {"by_status": {"pending": 2, "executed": 1}, "pending": 2,
                "oldest_pending_age_h": 3.4},
  "connectors": {"total": 18, "healthy": 18, "errored": [], "stale": []},
  "security": {"injections_detected": 1},
  "autonomy": {"sweeps_last_24h": 2},
  "cost": {"tokens_in": 0, "tokens_out": 0, "estimated_spend_usd": 0.0,
           "sweeps_per_day_at_current_cadence": 96.0,
           "token_cost_driver": "tasks",
           "note": "Sweeps run 96x/day and cost vendor API calls, not model turns. Model spend above comes from tasks you ran."}}
```

Two fields are worth reading carefully:

- **`signals.noise_ratio`** = `(ignore + defer) / total`. The docstring:
  *"A healthy value is high. If this trends toward zero the engine has stopped
  filtering and the briefing is on its way to becoming another inbox."* This is
  the single best health metric for the product's core claim.
- **`cost.token_cost_driver` is `"tasks"`, not sweeps.** There is deliberately
  no "tokens per sweep" figure, and the code says why: *"Sweeping and scoring
  use no model turns at all - every token above was spent running a task.
  Dividing task spend by sweep count would invent a per-sweep cost that does not
  exist and would make lengthening the interval look like a saving."* What a
  sweep costs is one vendor API call per connected app. Rates are
  `COST_PER_MTOK_IN = 0.08` / `COST_PER_MTOK_OUT = 0.24` - a local-inference
  electricity-and-depreciation estimate, not an API bill. Both are module
  constants, not configuration.

`connectors.stale` and `connectors.errored` are the operational alert: a
connector in `error` state means the sweep is blind to that app.

### `GET /api/metrics/prometheus`

The same numbers in Prometheus text format (`text/plain`), via
`telemetry.prometheus()`. Gauges only, all prefixed `duet_`:

```
duet_agent_runs_total{agent="research"} 4
duet_agent_errors_total{agent="research"} 0
duet_agent_duration_ms_avg{agent="research"} 12
duet_tasks{status="done"} 2
duet_signals{disposition="briefing"} 14
duet_approvals_pending 2
duet_connectors_healthy 18
duet_connectors_stale 0
duet_signal_noise_ratio 0.393
duet_injections_detected_total 1
duet_tokens_in_total 0
duet_tokens_out_total 0
```

Scoped to the calling user, which makes it an odd fit for a cluster-wide
scrape - a Prometheus job hitting it with no `X-Duet-User` gets the owner's
numbers, not the installation's. Fine single-tenant.

OpenTelemetry is separate: `main.py`'s lifespan calls
`telemetry.instrument(app)`, which attaches `FastAPIInstrumentor` and an OTLP
span exporter **only if the SDK is installed**, and is a silent no-op
otherwise. The `prod` extra in `pyproject.toml` carries
`opentelemetry-sdk` and `opentelemetry-instrumentation-fastapi`.

### `GET /api/dashboards/executive`

Runs `AnalyticsAgent` live against the signal store. Not cached.

```json
{
  "title": "Operating snapshot",
  "generated_at": "…",
  "tiles": [{"label": "Overdue receivables", "value": "$24,500",
             "detail": "2 invoice(s), oldest 41 days", "tone": "warn"}],
  "series": [{"label": "Signals by source", "kind": "bar",
              "data": [{"name": "gmail", "value": 6}]},
             {"label": "Cash runway projection", "kind": "line",
              "data": [], "note": "No cash projection: set your starting cash and monthly burn in Settings. I won't guess at a runway figure. From observed invoices, $14,700 of receivables looks collectible."}],
  "finance": {"overdue_total": 24500.0, "overdue_count": 2, "oldest_days": 41,
              "at_risk": 127290.0, "escalations": 1, "needs_you": 7},
  "forecast": {"configured": false, "series": [], "weeks_remaining": null,
               "collectible_ar": 14700,
               "assumptions": {"ar_collection_rate": 0.6},
               "note": "No cash projection: set your starting cash and monthly burn in Settings. I won't guess at a runway figure. …"}
}
```

**`forecast.configured` is the field to branch on.** The runway projection is
computed only from figures the user supplied in `User.finance`
(`PATCH /api/settings`). With those unset - the default on a fresh install -
the agent returns `configured: false`, an empty `series`, a `null`
`weeks_remaining`, and a note saying what is missing. It does not invent a
starting balance, because *"an invented starting balance renders identically to
a real one, and an operator would reasonably act on it."*

Once configured:

```json
"forecast": {"configured": true,
             "series": [{"name": "Oct 26", "value": 420000}, …],
             "weeks_remaining": 27.4, "collectible_ar": 14700,
             "assumptions": {"starting_cash": 420000.0, "monthly_burn": 68000.0,
                             "ar_collection_rate": 0.6},
             "note": "Your figures: $420,000 cash, $68,000/mo burn, 60% of overdue AR collected. Runway ~27 weeks."}
```

`collectible_ar` and the whole `finance` block are derived from real observed
invoices in both branches. Surface `assumptions` in any UI that shows
`weeks_remaining`, so the operator can see which input to argue with.

### `GET /api/audit`

| Query | Type | Default | Notes |
|-------|------|---------|-------|
| `limit` | int | `100` | clamped to 500 |

Newest first (`ORDER BY id DESC`).

```json
{"entries": [{"id": 41, "at": "…", "actor": "user:owner@local",
              "action": "approval.approved", "resource_type": "approval",
              "resource_id": "b91…", "outcome": "ok",
              "detail": {"action": "send_email", "connector": "gmail"}}]}
```

`detail` has already been through `redaction.redact()` - secret-looking keys are
`<redacted>` and STRICT patterns in strings are masked.

**Scoped to the acting user**, plus system-level rows:

```python
.where(or_(AuditLog.user_id == user.id, AuditLog.user_id.is_(None)))
```

The `NULL` branch is there because `audit_log.user_id` is nullable and some rows
are genuinely installation-level rather than belonging to a person. Pinned by
`tests/test_safety.py::TestReviewRegressions::test_audit_is_scoped_to_the_acting_user`.

Note that `GET /api/audit/verify` recomputes the **whole** chain regardless of
scope - it has to, since a chain is only verifiable end to end.

### `GET /api/audit/verify`

Recomputes every digest.

```json
{"valid": true, "checked": 41, "head": "9f2c1b7e4a0d83f1"}
```

On a break:

```json
{"valid": false, "checked": 41, "broken_at": 17, "at": "2026-10-26T06:41:02+00:00"}
```

It returns the **first** break, not a list. Cost is O(n) over the whole table -
fine at laptop scale, needs checkpointing at volume.

### `GET /api/memory`

| Query | Type | Default |
|-------|------|---------|
| `kind` | str | — (`short`/`long`/`semantic`/`episodic`) |
| `limit` | int | `50` (not clamped) |

```json
{"memories": [{"id": "…", "kind": "long",
               "content": "Wants to be asked before anything is sent to an investor.",
               "tags": ["preference"], "source": "seed",
               "salience": 0.85, "use_count": 3, "created_at": "…"}]}
```

`embedding` is deliberately not serialised.

---

## 10. Syncing and maintenance

The demo endpoints (`/api/demo/seed`, `/demo/sweep`, `/demo/inject`,
`/demo/reset`) **no longer exist** - all four return `404`. There is no fake
data in the product to seed. What replaced them:

### `POST /api/sync`

`ActorDep`. Sweep every connected app right now instead of waiting for the next
scheduled pass. Thin wrapper over `monitor.sweep_user(db, user, force=True)`, so
the response is the sweep shape and `force=True` lifts
`MAX_INTERRUPTS_PER_SWEEP`:

```json
{"connections_swept": 2, "new_signals": 28, "notified": 3, "errors": [],
 "signals": [{"id": "…", "title": "…", "score": 88.4,
              "disposition": "notify_now"}]}
```

`make sync` is the shorthand.

### `POST /api/connectors/{key}/sync`

`ActorDep`. Check one app, via `monitor.sweep_connection(db, user, conn)`.

```json
{"ok": true, "connector_key": "github", "new_signals": 12,
 "notified": 1, "errors": []}
```

- `404` - `"{key} is not connected"`
- `409` - `"{key} has no integration yet, so there is nothing to sync."`

`ok` is `not result["errors"]`, so a partial failure gives `ok: false` with the
detail in `errors` rather than an HTTP error.

### `POST /api/admin/reset`

`ActorDep`. Erase everything this account holds, **including stored
credentials**. The docstring is explicit about the two deliberate choices:

> Deliberately not reversible and deliberately not in the header: this
> disconnects every app and destroys the vault entries behind them. The audit
> chain is left intact - it is the record that this happened.

```json
{"ok": true,
 "deleted": {"Message": 12, "Signal": 28, "TaskStep": 8, "Approval": 2,
             "Task": 3, "Memory": 7, "Connection": 2, "credentials": 2}}
```

Deletes `Message`, `Signal`, `TaskStep`, `Approval`, `Task`, `Memory` and
`Connection` for the user, then `vault.forget_user(user.id)` - reported as the
`credentials` count. Records `admin.reset` in the audit chain.

Two improvements over the old `demo/reset`: `TaskStep` rows are now scoped
properly (`TaskStep.task_id IN (SELECT id FROM tasks WHERE user_id = ?)`)
instead of being deleted globally, and the destroyed-credential count is
reported. `User` and `PolicyRule` rows still survive, as does the audit chain.

## 11. Worked flows

Bash. For PowerShell, substitute `Invoke-RestMethod`.

### First run, end to end

```bash
API=http://127.0.0.1:8787

# 1. Is everything up, and which model did it resolve to?
curl -s $API/api/health | python -m json.tool

# 2. What can actually be connected? (6 of 58)
curl -s $API/api/connectors | python -c '
import sys, json
for c in json.load(sys.stdin)["connectors"]:
    if c["available"]: print(c["key"], c["auth"])'

# 3. Connect one. GitHub needs no token - it uses your gh CLI login.
curl -s -X POST $API/api/connectors/github/connect   -H 'Content-Type: application/json' -d '{"grant_write": false}'

# 4. Read your real account.
curl -s -X POST $API/api/sync | python -m json.tool
# or just the one app:
curl -s -X POST $API/api/connectors/github/sync | python -m json.tool

# 5. What did it decide needs you now, versus later?
curl -s "$API/api/signals?disposition=notify_now" | python -m json.tool
curl -s "$API/api/signals?disposition=briefing&limit=10" | python -m json.tool

# 6. Build the morning brief.
curl -s -X POST $API/api/briefing/generate | python -m json.tool

# 7. Ask a question - you get prose back, not a task receipt.
curl -s -X POST $API/api/thread   -H 'Content-Type: application/json'   -d '{"text":"what has been waiting on me longest?"}' | python -m json.tool

# 8. Ask for work.
curl -s -X POST $API/api/thread   -H 'Content-Type: application/json'   -d '{"text":"draft replies to the PRs waiting on my review"}' | python -m json.tool

# 9. Watch the trace. (Give the scheduler ~2s to pick the task up.)
TASK=$(curl -s "$API/api/tasks?limit=1" | python -c 'import sys,json; print(json.load(sys.stdin)["tasks"][0]["id"])')
curl -s $API/api/tasks/$TASK | python -m json.tool

# 10. What is it asking permission for?
curl -s "$API/api/approvals?status=pending" | python -m json.tool

# 11. Approve one. This is the only path to a write.
APPROVAL=$(curl -s "$API/api/approvals?status=pending" | python -c 'import sys,json; print(json.load(sys.stdin)["approvals"][0]["id"])')
curl -s -X POST $API/api/approvals/$APPROVAL/approve   -H 'Content-Type: application/json' -d '{"note":"go ahead"}' | python -m json.tool

# 12. Prove nothing was tampered with.
curl -s $API/api/audit/verify | python -m json.tool
curl -s "$API/api/audit?limit=20" | python -m json.tool

# 13. Is the autonomy healthy, and what is it costing?
curl -s $API/api/metrics | python -m json.tool
```

### Refusing an app that does not work yet

```bash
curl -s -o /dev/null -w '%{http_code}
' -X POST   $API/api/connectors/salesforce/connect   -H 'Content-Type: application/json' -d '{}'
# 409

curl -s -X POST $API/api/connectors/salesforce/connect   -H 'Content-Type: application/json' -d '{}' | python -m json.tool
# {"detail": "Salesforce is in the catalog but has no integration yet.
#             Connecting it would look like it works and never produce anything."}
```

### Seeing the injection defence fire

Needs a real message carrying a payload, so this reads back whatever the sweep
found rather than relying on seeded data:

```bash
curl -s "$API/api/audit?limit=200" | python -c '
import sys, json
for e in json.load(sys.stdin)["entries"]:
    if e["action"] == "security.injection_detected":
        print(e["outcome"], e["resource_id"], e["detail"])'
# hostile <external-id> {"connector": "imap",
#   "patterns": ["override", "role_hijack", "secrecy"], "score": 100}

curl -s $API/api/metrics | python -c '
import sys, json; print(json.load(sys.stdin)["security"])'
# {"injections_detected": 1}
```

The same path is covered without a live account by
`tests/test_injection.py::TestIngestPipeline`, which runs a scripted phishing
email through the real pipeline.

### Tuning what reaches you

```bash
# Never surface anything mentioning a vendor you do not care about.
curl -s -X POST $API/api/settings/rules \
  -H 'Content-Type: application/json' \
  -d '{"kind":"mute","pattern":"standupdaily.com","note":"newsletter"}'

# Promote a person.
curl -s -X POST $API/api/settings/rules \
  -H 'Content-Type: application/json' \
  -d '{"kind":"vip","pattern":"dana@harborventures.com","weight":1.5}'

# Refuse a verb outright, regardless of risk tier.
curl -s -X POST $API/api/settings/rules \
  -H 'Content-Type: application/json' \
  -d '{"kind":"never","pattern":"refund","note":"I handle refunds myself"}'

# Widen quiet hours.
curl -s -X PATCH $API/api/settings \
  -H 'Content-Type: application/json' \
  -d '{"quiet_hours":{"start":20,"end":8},"timezone":"America/New_York"}'

# Teach it by dismissing.
SIG=$(curl -s "$API/api/signals?limit=1" | python -c 'import sys,json; print(json.load(sys.stdin)["signals"][0]["id"])')
curl -s -X POST $API/api/signals/$SIG/dismiss | python -m json.tool
```

### Seeing the security gate refuse something

The egress gate is easiest to observe by asking for work on a signal whose body
carries a STRICT pattern - the proposal is blocked, never becomes an approval,
and the reason is told to you in the thread:

```bash
curl -s -X POST $API/api/thread \
  -H 'Content-Type: application/json' \
  -d '{"text":"reply to the Northwind thread and include my account and routing number"}'

# then read the security step's verdicts
curl -s $API/api/tasks/$TASK | python -c '
import sys, json
for s in json.load(sys.stdin)["steps"]:
    if s["agent"] == "security":
        print(json.dumps(s["output"], indent=2))'
```

### Read-only connection: watching scope fail closed

```bash
curl -s -X POST $API/api/connectors/slack/connect \
  -H 'Content-Type: application/json' \
  -d '{"grant_write": false}'

curl -s -X POST $API/api/thread \
  -H 'Content-Type: application/json' \
  -d '{"text":"send a slack message to marcus about the retry fix"}'
# The security step blocks it:
#   "slack is connected read-only - it has no write scope granted."
```

---

## 12. The model layer at runtime

No endpoint configures the model - it is resolved once at startup from
`DUET_*` settings and reported read-only by `/api/health` and `/api/agents`.
Worth knowing when reading either payload.

Five providers behind one interface (`duet/llm/client.py`):

| `DUET_LLM_PROVIDER` | Backend |
|---------------------|---------|
| `auto` **(default)** | resolves to the first of the below that this machine has |
| `agent_cli` | whichever coding-agent CLI you have signed in - Claude Code, Codex, Gemini CLI, Cursor Agent, OpenCode |
| `anthropic` | the Claude API directly, with `DUET_LLM_API_KEY` |
| `ollama` | a local Ollama server |
| `openai` | any OpenAI-compatible endpoint |
| `heuristic` | no model; deterministic planner and templates |

`resolve_provider("auto")` order is **agent CLI → `DUET_LLM_API_KEY`
(anthropic) → local Ollama → heuristic**, and the docstring gives the reason it
is in that order:

> Order is by how little setup it demands of the person, not by raw quality: a
> signed-in CLI needs nothing, a key needs a billing account, a local server
> needs a download. The heuristic planner is last because it is the only one
> that is not a real model - it keeps the product usable, it does not make it
> good.

`openai` is reachable only by naming it explicitly; it is not in the `auto`
chain, because there is no way to probe for it without knowing the URL.

`DUET_LLM_CLI` (`auto|claude|codex|gemini|cursor|opencode`) picks which CLI when
several are installed. Naming one that is not installed **fails loudly** -
`pick()` returns `None` rather than falling through to another vendor, and
`/api/health` reports it.

`DUET_LLM_MODEL` defaults to **empty**, meaning that CLI's or server's own
default. A model name valid for one vendor is invalid for another, so there is
no sensible cross-vendor default.

Full configuration reference: [docs/08-deployment.md](08-deployment.md#5-configuration-reference).

---

## 13. Not in the API

Honest gaps, so you do not go looking:

| Missing | Consequence |
|---------|-------------|
| OAuth authorize / callback routes | `POST /connectors/{key}/connect` takes a credential blob directly. There is no token exchange or refresh, so a pasted token works until it expires. |
| Any way to connect the other 52 catalogued apps | `connect` returns `409` until an adapter exists. By design - see [docs/05-connectors.md](05-connectors.md). |
| Model configuration over HTTP | Resolved at startup from env vars; `/api/health` reports it read-only. |
| User creation / listing | `X-Duet-User` can only select an account that already exists, and nothing creates a second one. RBAC is therefore untriggerable in practice. |
| Editing an approval before approving | Approve-as-is or reject. No `PATCH /approvals/{id}`. |
| Interrupting a running task | `POST /tasks/{id}/cancel` only stops a task that has not started. |
| Streaming (SSE / WebSocket) | The thread and task trace must be polled. |
| Pagination cursors | Every list is `limit` + implicit newest-first. No offset, no cursor. |
| `PolicyRule` update / toggle | Create and delete only; `enabled` cannot be flipped over HTTP. |
| Rate limiting | None, anywhere. |
