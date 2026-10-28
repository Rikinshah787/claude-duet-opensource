# 05 - Connectors

In `apps/api/duet/connectors/`:

| File | Role |
|------|------|
| `base.py` | the `Connector` protocol and its four data types |
| `catalog.py` | 58 `ConnectorSpec` entries - static metadata and risk declarations |
| `registry.py` | `resolve(key)` → a live adapter, else `UnavailableConnector` |
| `live/` | the 6 real adapters: `github_cli`, `imap_mail`, `linear`, `notion`, `slack`, `stripe` |

**Current state, stated plainly: 6 of 58 catalogued apps work. There is no fake
adapter anywhere in the shipped package.**

```bash
cd apps/api && python -c "from duet.connectors import registry; print(registry.live_keys())"
# ['github', 'imap', 'linear', 'notion', 'slack', 'stripe']
```

The other 52 resolve to `UnavailableConnector`, which returns nothing and says
why. `registry.py`'s docstring draws the line the whole module exists to hold:

> There is no fake adapter here. An app in the catalog without a real
> integration resolves to `UnavailableConnector`, which returns nothing and says
> why - it does not invent email, invoices or tickets. The catalog is a
> statement of what the product intends to support; `available()` is the
> statement of what works today, and the UI shows the difference rather than
> blurring it.

What is still missing at the edge: **no OAuth flow.** `connect` takes a
credential blob directly, so there is no authorize redirect, no code exchange
and no refresh, and `Connection.scopes` records what was requested rather than
what a provider granted. Two of the six sidestep that entirely - GitHub reuses
your `gh` CLI login and IMAP takes an app password.

The scripted scenario that used to live here as `demo.py` is now
`apps/api/tests/fixtures/scenario.py`, a test-only fixture. It is not importable
from the product.

---

## 1. The protocol

`base.py` splits reads from writes, and that split is the security boundary:

```python
@runtime_checkable
class Connector(Protocol):
    key: str

    def fetch(self, ctx: FetchContext) -> tuple[list[RawSignal], dict[str, Any]]:
        """Return new observations and the updated cursor. Read-only."""

    def execute(self, action: str, payload: dict[str, Any], ctx: FetchContext) -> ActionResult:
        """Perform one approved write. Called only by the approval executor."""
```

- `fetch()` has exactly one caller: `core/monitor.py::_sweep_connection()`,
  which runs unattended. Because it can only read, unattended operation is
  defensible.
- `execute()` has exactly two callers, both in `core/executor.py`
  (`execute()` and `revert()`). Nothing else in the tree calls it. That is one
  file to audit for the question "can it act without asking?".

`BaseConnector` is the safe default:

```python
class BaseConnector:
    key: str = "base"

    def fetch(self, ctx): return [], ctx.cursor
    def execute(self, action, payload, ctx):
        return ActionResult(ok=False,
                            summary=f"{self.key}.{action} is not implemented",
                            error="not_implemented")
```

Reads return nothing; writes **fail closed**. A half-built adapter cannot
silently report success.

### `RawSignal`

An observation, before scoring.

```python
@dataclass
class RawSignal:
    external_id: str                 # the vendor's id → uq_conn_external dedupe
    kind: str                        # must be a key the priority engine knows
    title: str
    body: str = ""
    actor: str = ""                  # who it came from
    url: str | None = None           # deep link back to the source
    occurred_at: datetime | None = None   # VENDOR time, not ingest time
    raw: dict[str, Any] = {}
    hints: dict[str, Any] = {}       # evidence for the priority engine
```

`hints` is the adapter's channel into scoring, and the docstring draws the line:

> Adapter-supplied hints. The priority engine treats these as evidence, not as a
> verdict - a connector cannot promote itself straight to a notification.

There is no `hints["disposition"]` or `hints["score"]`. An adapter can assert
`{"amount": 18400, "days_overdue": 41, "churn_risk": True}` and the engine
decides what that is worth. The hint keys the engine actually reads:

| Hint | Read by | Worth |
|------|---------|-------|
| `deadline` (ISO string) | `urgency_factor` | up to +28 |
| `days_overdue` (int) | `urgency_factor` | `min(18, days * 0.4)` |
| *(the signal's `kind`)* | `urgency_factor` | for the 9 `WAITING_ON_YOU` kinds, **age adds** up to +14 instead of decaying - see below |
| `amount` (number) | `financial_factor` | +3 to +26, log-ish bands |
| `vip` | `relationship_factor` | +16 |
| `customer` | `relationship_factor` | +10 |
| `churn_risk` | `relationship_factor` | +14 |
| `external` | `relationship_factor` | +4 |
| `sentiment: "negative"` | `relationship_factor` | +8 |
| `importance: "high"` | `importance_factor` | +12 |
| `blocker` | `importance_factor` | +10 |
| `escalation` | `importance_factor` | +12 |
| `conflict` | `importance_factor` | +10 |
| `prep_needed` | `importance_factor` | +6 |
| `persistent` | `importance_factor` | +6 |
| `legal` / `sensitive` | `importance_factor` | +8 |
| `links_to` | `importance_factor` | +5 |
| `bulk` / `list_unsubscribe` | `noise_factor` | **-22** |
| `social` | `noise_factor` | **-14** |
| `receipt` | `noise_factor` | **-10** |
| `channel_broadcast` (without `mention`) | `noise_factor` | **-10** |
| `automated` | `noise_factor` | **-6** |

Any other key is stored in `signals.raw["hints"]` and ignored by scoring.
`hints` must be JSON-serialisable - it is persisted.

### Age cuts both ways, depending on the kind

`urgency_factor(occurred_at, hints, kind)` changes the **direction** of the age
term based on who is waiting:

```python
WAITING_ON_YOU = frozenset({
    "review_request", "thread_waiting", "signature_pending", "share_request",
    "dm", "escalation", "blocker", "deal_stale", "invoice_overdue",
})
```

For everything else, old means less relevant: `-4` past 72 hours. For these
nine, old means **more overdue**: `+4` after a day, `+9` after three days,
`+14` after two weeks.

It takes `max(freshness, overdue)` rather than replacing one with the other, and
the comment says what that fixes:

> Both ends of the curve matter here, so take whichever is stronger rather than
> replacing one with the other: a customer escalation is urgent in its first
> hour *and* damning after three weeks. Dropping the freshness term entirely
> silently de-escalated live incidents.

This was found against real data: 28 live GitHub notifications all scored
`ignore`, because a review request that had been sitting for three weeks was
being penalised for being old. If your adapter emits one of these nine kinds,
`occurred_at` accuracy is load-bearing - see the adapter rules in
[section 6](#6-adding-the-seventh-connector).

One key is added for you: `monitor._sweep_connection()` runs
`injection.screen_signal(raw.title, raw.body)` **before** scoring and, on a hit,
writes `hints["injection"] = {score, suspicious, hostile, matches, excerpts}`
and audits it. Adapters do not need to do anything for this; do not set the key
yourself.

Downstream, `hints["injection"]["hostile"]` removes the signal from the agents'
working set entirely (`workers._recent_signals()`), and any
`hints["injection"]` at all marks the finding `flagged` so the communication
agent will not address a reply to that sender. So a signal your adapter returns
can be stored, scored and audited while still being excluded from research
results - by design.

### `ActionResult`

```python
@dataclass
class ActionResult:
    ok: bool
    summary: str                     # shown to the user verbatim in the thread
    data: dict[str, Any] = {}
    external_id: str | None = None   # the created/affected vendor object
    revert_plan: dict[str, Any] = {} # how to undo this, if it can be undone
    error: str | None = None
```

`summary` is user-facing - the executor puts it straight into a thread message,
so write it the way you would want to read it ("Email sent to
priya@northwind.io", not "200 OK").

`revert_plan` is passed back to your own `execute()` later:

```python
# core/executor.py::revert
plan = approval.revert_plan
result = adapter.execute(plan.get("action", "revert"), plan, ctx)
```

So the plan is self-describing - it must contain `"action"` and whatever your
adapter needs to undo the thing. Return `{}` for anything you genuinely cannot
reverse; `revert()` then refuses with a message naming the app the user has to
go fix it in.

### `CredentialHandle`

```python
@dataclass
class CredentialHandle:
    ref: str
    connector_key: str

    def __repr__(self) -> str:   # never let a secret-ish value reach a log
        return f"<CredentialHandle {self.connector_key}:{self.ref[:6]}...>"
```

An opaque pointer into the vault. The custom `__repr__` is there because the
most common way a secret leaks is a developer logging an object that happens to
contain one.

**The handle is not the secret.** To get the secret you must call
`vault.inject(handle, fn)`, which resolves it, passes it to `fn`, and returns
only `fn`'s result - there is no code path that hands a raw credential back up
the stack. See [docs/06-security.md](06-security.md).

### `FetchContext`

```python
@dataclass
class FetchContext:
    user_id: str
    connection_id: str
    since: datetime | None               # Connection.last_sync_at
    cursor: dict[str, Any]               # yours; round-tripped through the DB
    credential: CredentialHandle | None
```

`cursor` is adapter-owned opaque state. Return an updated one from `fetch()`
and it is persisted to `connections.cursor` for the next sweep. Use it for page
tokens, delta links, high-water marks. It must be JSON-serialisable.

`since` is advisory. The engine's actual correctness guarantee against
duplicates is `uq_conn_external` in the database, not your `since` filtering -
so an adapter that over-returns is inefficient, not wrong.

The same `FetchContext` type is used for writes, which is a slight misnomer
(`since` is always `None` there and `cursor` is only populated on the forward
path) but keeps one credential-passing shape.

---

## 2. The catalog

`catalog.py` holds static metadata only. The docstring:

> The catalog is static metadata only - it declares what an app *is*, what it
> can be read for, and which of its write actions need a human. Runtime adapters
> are resolved in `registry.py`; an app without one resolves to
> `UnavailableConnector` and returns nothing rather than inventing data.
> `has_adapter` says whether a real integration exists today.
>
> Risk tiers on write actions are the contract the approval gate enforces. They
> are declared here, next to the action, so adding a connector forces the author
> to say what the dangerous verbs are.

`has_adapter` means **a real integration exists**, not "an adapter is intended".
The field comment is explicit (`# a REAL integration exists (see registry.REAL)`)
and the two are kept identical:

```bash
cd apps/api && python -c "
from duet.connectors.catalog import CATALOG
from duet.connectors import registry
ha = sorted(c.key for c in CATALOG if c.has_adapter)
print(ha == registry.live_keys(), ha)"
# True ['github', 'imap', 'linear', 'notion', 'slack', 'stripe']
```

If you add an adapter, flip `has_adapter` in the same commit. A drift between
those two is a UI that offers an app that cannot work.

```python
@dataclass(frozen=True)
class WriteAction:
    key: str                  # matches Proposal.action_type and Approval.action_type
    label: str                # human label for the UI
    risk: RiskTier
    reversible: bool = True
    note: str = ""            # shown next to the approval; say WHY it is risky

@dataclass(frozen=True)
class ConnectorSpec:
    key: str
    name: str
    category: str
    color: str                       # brand hex for the UI tile
    glyph: str                       # short fallback mark
    summary: str
    auth: str = "oauth2"             # oauth2 | api_key | basic | app_password
    read_scopes: tuple[str, ...] = ()
    write_scopes: tuple[str, ...] = ()
    signal_kinds: tuple[str, ...] = ()
    writes: tuple[WriteAction, ...] = ()
    monitored: bool = True           # included in the proactive sweep
    has_adapter: bool = False        # a runtime adapter exists
    docs: str = ""
    tags: tuple[str, ...] = ()
```

Both are `frozen=True`: the catalog is immutable at runtime. Risk cannot be
mutated by a running agent.

### How risk is enforced

Three lookups, all in `catalog.py`:

```python
def write_action(connector_key, action_key) -> WriteAction | None: ...

def risk_for(connector_key, action_key) -> RiskTier:
    """Unknown verbs on known connectors are treated as sensitive, not safe.

    Defaulting an unrecognised action to SAFE would mean a typo silently skips
    the approval gate.
    """
    action = write_action(connector_key, action_key)
    return action.risk if action else RiskTier.SENSITIVE
```

`risk_for()` is called in two independent places:

1. `agents/security.py::review()` - `risk = declared`, **overwriting** whatever
   the proposing agent claimed.
2. `core/executor.py::_preflight()` - re-checked at execution time, because
   the catalog can change between approval and execution.

The practical consequence: to make an action require a human, you edit one line
in the catalog. To make one *not* require a human, there is no line to edit -
every proposal becomes an `Approval` row regardless of tier.

### Catalog facts

| | |
|---|---|
| Connector specs | **58** |
| Categories | 16 |
| Included in the sweep (`monitored=True`) | 51 |
| **Working today** (`has_adapter=True` = `registry.REAL`) | **6** |
| Catalogued but unavailable | 52 |
| Write actions declared | 92 |
| → `safe` | 26 |
| → `review` | 39 |
| → `sensitive` | 25 |
| → `forbidden` | 2 |
| Irreversible write actions | 35 |
| Auth kinds | `oauth2` 52, `api_key` 3, `app_password` 2, `basic` 1 |

The 7 connectors excluded from the sweep (`monitored=False`) are `miro`,
`canva`, `adobe`, `tiktok`, `1password`, `postgres` and `custom_api` - things
you reach *into* rather than things that notify you.

The two `forbidden` verbs are `stripe.transfer_balance` and
`paypal.send_payment`. Both are also in
`agents/security.py::FORBIDDEN_ACTIONS`, so they are blocked by two independent
mechanisms.

---

## 3. Full catalog

Grouped by category, in `CATEGORIES` order. "Highest risk" is the most
dangerous verb the connector declares - the number to look at when deciding how
much you trust an app. **"Works today"** is `has_adapter` / `registry.REAL` -
six of fifty-eight; everything else returns nothing and refuses to connect.

| key | name | category | watches (signal kinds) | can write | highest risk | swept | **works today** |
|-----|------|----------|------------------------|-----------|--------------|-------|-----------------|
| `discord` | Discord | communication | mention, dm | `post_message` | `review` | yes | no |
| `gmail` | Gmail | communication | email, thread_waiting, vip_email | `create_draft`, `send_email`, `archive`, `delete_thread` | `sensitive` | yes | no |
| `imap` | Email (IMAP) | communication | email, thread_waiting | - | `-` | yes | **yes** |
| `outlook` | Outlook | communication | email, thread_waiting | `create_draft`, `send_email` | `review` | yes | no |
| `slack` | Slack | communication | mention, dm, thread_waiting, escalation | `post_message`, `set_status` | `review` | yes | **yes** |
| `teams` | Microsoft Teams | communication | mention, dm, meeting | `post_message` | `review` | yes | no |
| `calendly` | Calendly | calendar | meeting, booking | `cancel_booking` | `review` | yes | no |
| `google_calendar` | Google Calendar | calendar | meeting, conflict, prep_needed | `create_event`, `move_event`, `cancel_event` | `sensitive` | yes | no |
| `outlook_calendar` | Outlook Calendar | calendar | meeting, conflict | `create_event`, `cancel_event` | `sensitive` | yes | no |
| `zoom` | Zoom | calendar | meeting, recording_ready | `create_meeting` | `review` | yes | no |
| `airtable` | Airtable | productivity | doc_update | `create_record` | `safe` | yes | no |
| `confluence` | Confluence | productivity | doc_update, mention | `create_page` | `safe` | yes | no |
| `google_sheets` | Google Sheets | productivity | doc_update | `write_range` | `review` | yes | no |
| `microsoft_365` | Microsoft 365 | productivity | doc_update | `update_file` | `review` | yes | no |
| `notion` | Notion | productivity | doc_update, mention | `create_page`, `update_page`, `delete_page` | `sensitive` | yes | **yes** |
| `todoist` | Todoist | productivity | task_due | `create_task` | `safe` | yes | no |
| `attio` | Attio | crm | contact_activity | `update_record` | `review` | yes | no |
| `hubspot` | HubSpot | crm | deal_stale, deal_risk, contact_activity | `log_activity`, `update_deal`, `delete_record` | `sensitive` | yes | no |
| `salesforce` | Salesforce | crm | deal_stale, deal_risk | `update_opportunity`, `delete_record` | `sensitive` | yes | no |
| `asana` | Asana | project | task_due, deadline | `create_task` | `safe` | yes | no |
| `basecamp` | Basecamp | project | mention, task_due | `post_message` | `review` | yes | no |
| `clickup` | ClickUp | project | task_due, deadline | `create_task` | `safe` | yes | no |
| `jira` | Jira | project | ticket, blocker, deadline | `create_issue`, `transition_issue`, `delete_issue` | `sensitive` | yes | no |
| `linear` | Linear | project | ticket, blocker, deadline | `create_issue`, `update_issue` | `review` | yes | **yes** |
| `monday` | monday.com | project | task_due, deadline | `create_item` | `safe` | yes | no |
| `trello` | Trello | project | task_due | `create_card` | `safe` | yes | no |
| `docusign` | Docusign | finance | signature_pending, deadline | `prepare_envelope`, `send_for_signature`, `sign_document` | `sensitive` | yes | no |
| `paypal` | PayPal | finance | payment_failed, dispute | `issue_refund`, `send_payment` | **`forbidden`** | yes | no |
| `quickbooks` | QuickBooks | finance | invoice_overdue, bill_due, cash_risk | `draft_invoice`, `send_invoice`, `pay_bill` | `sensitive` | yes | no |
| `square` | Square | finance | revenue_risk, payment_failed | `issue_refund` | `sensitive` | yes | no |
| `stripe` | Stripe | finance | payment_failed, dispute, invoice_overdue, revenue_risk | `draft_invoice`, `send_invoice`, `issue_refund`, `transfer_balance` | **`forbidden`** | yes | **yes** |
| `xero` | Xero | finance | invoice_overdue, bill_due | `draft_invoice`, `send_invoice` | `review` | yes | no |
| `shopify` | Shopify | commerce | order, inventory_low, revenue_risk | `update_product`, `publish_theme` | `sensitive` | yes | no |
| `gusto` | Gusto | hr | payroll_due, deadline | `prepare_payroll`, `submit_payroll` | `sensitive` | yes | no |
| `box` | Box | storage | doc_update, share_request | `share_file` | `sensitive` | yes | no |
| `dropbox` | Dropbox | storage | doc_update, share_request | `share_file` | `sensitive` | yes | no |
| `google_drive` | Google Drive | storage | doc_update, share_request | `create_file`, `share_file`, `delete_file` | `sensitive` | yes | no |
| `onedrive` | OneDrive | storage | doc_update, share_request | `share_file` | `sensitive` | yes | no |
| `sharepoint` | SharePoint | storage | doc_update | `update_file` | `review` | yes | no |
| `github` | GitHub | development | review_request, ci_failure, mention | `comment`, `open_pr`, `merge_pr` | `sensitive` | yes | **yes** |
| `gitlab` | GitLab | development | review_request, ci_failure | `comment` | `review` | yes | no |
| `sentry` | Sentry | development | incident, ci_failure | `resolve_issue` | `review` | yes | no |
| `vercel` | Vercel | development | ci_failure, incident | `rollback` | `sensitive` | yes | no |
| `adobe` | Adobe Creative Cloud | design | - | `export_asset` | `safe` | no | no |
| `canva` | Canva | design | - | `create_design` | `safe` | no | no |
| `figma` | Figma | design | mention, doc_update | `comment` | `review` | yes | no |
| `miro` | Miro | design | mention | `create_board` | `safe` | no | no |
| `linkedin` | LinkedIn | marketing | mention | `publish_post` | `review` | yes | no |
| `mailchimp` | Mailchimp | marketing | campaign_result | `create_campaign`, `send_campaign` | `sensitive` | yes | no |
| `tiktok` | TikTok | marketing | campaign_result | `publish_video` | `review` | no | no |
| `intercom` | Intercom | support | escalation, customer_message | `reply` | `review` | yes | no |
| `zendesk` | Zendesk | support | ticket, escalation, sla_breach | `comment`, `update_ticket` | `review` | yes | no |
| `1password` | 1Password | security | credential_expiry | `use_login` | `sensitive` | no | no |
| `okta` | Okta | security | security_alert, credential_expiry | `change_permissions` | `sensitive` | yes | no |
| `n8n` | n8n | automation | automation_failed | `run_workflow` | `review` | yes | no |
| `zapier` | Zapier | automation | automation_failed | `run_zap` | `review` | yes | no |
| `custom_api` | Custom API | data | metric_anomaly | `call_read`, `call_write` | `review` | no | no |
| `postgres` | PostgreSQL | data | metric_anomaly | `run_query`, `run_mutation` | `sensitive` | no | no |

### Every declared `note`

These strings are shown next to the approval. They are the sentence that
explains to a human what they are about to allow, and writing a good one is part
of adding a connector.

| Action | Tier | Note |
|--------|------|------|
| `gusto.prepare_payroll` | `safe` | Fills hours and bonuses; does not submit. |
| `gusto.submit_payroll` | `sensitive` | Moves money to employees. |
| `stripe.issue_refund` | `sensitive` | Moves money out. Always requires explicit approval. |
| `stripe.transfer_balance` | `forbidden` | Blocked by policy, even with approval. |
| `mailchimp.send_campaign` | `sensitive` | Reaches every subscriber; cannot be recalled. |
| `docusign.sign_document` | `sensitive` | Legally binding. |
| `google_drive.share_file` | `sensitive` | Changes who can see the contents. |
| `shopify.publish_theme` | `sensitive` | Changes what customers see immediately. |
| `vercel.rollback` | `sensitive` | Changes what is live in production. |
| `okta.change_permissions` | `sensitive` | Alters who can reach what. |
| `zapier.run_zap` | `review` | Effect depends on the Zap; treated as a send. |
| `1password.use_login` | `sensitive` | Credential is injected in the sandbox, never shown to the model. |

The Gusto pair is the pattern worth copying: **split a dangerous verb into a
prepare step and a commit step**, make the prepare step `safe`, and let the
assistant do all the work up to the irreversible moment. The same shape appears
as `draft_invoice` / `send_invoice` and `prepare_envelope` /
`send_for_signature`.

`zapier.run_zap` is the honest-uncertainty case: the catalog cannot know what a
user's Zap does, so it is rated as a send rather than as safe.

---

## 4. The registry

```python
# registry.py
REAL: dict[str, type[BaseConnector]] = {}      # populated by _register_live()
_cache: dict[str, BaseConnector] = {}

def register(connector_key: str, adapter_cls: type[BaseConnector]) -> None:
    """Add or replace an adapter. Used by live registration and by tests."""
    REAL[connector_key] = adapter_cls
    _cache.pop(connector_key, None)

def _register_live() -> None:
    from .live import github_cli, imap_mail, linear, notion, slack, stripe
    register("github", github_cli.GitHubConnector)
    register("notion", notion.NotionConnector)
    register("linear", linear.LinearConnector)
    register("stripe", stripe.StripeConnector)
    register("slack",  slack.SlackConnector)
    register("imap",   imap_mail.ImapConnector)

try:
    _register_live()
except Exception as exc:        # one bad import disables one app, not the API
    log.warning("some live adapters failed to load: %s", exc)

def resolve(connector_key: str) -> BaseConnector:
    if connector_key in _cache:  return _cache[connector_key]
    adapter = REAL[connector_key]() if connector_key in REAL               else UnavailableConnector(connector_key)
    _cache[connector_key] = adapter
    return adapter
```

Two levels, not three: a real adapter, or `UnavailableConnector`. There is no
fake middle tier any more.

| Function | Returns |
|----------|---------|
| `register(key, cls)` | — adds or replaces an adapter and evicts its cache entry |
| `resolve(key)` | the adapter instance (cached) |
| `is_live(key)` | `key in REAL` |
| `available(key)` | `key in REAL and key in BY_KEY` - can this be connected and used today |
| `live_keys()` | `sorted(REAL)` |
| `reset_cache()` | clears `_cache`, for tests that swap adapters |

`_register_live()` is wrapped in `try/except` deliberately: *"so that one adapter
failing to import (a missing dependency, a syntax error) disables only that app
instead of taking the API down at startup."*

`_cache` means **one adapter instance per key for the process lifetime**, shared
across users and threads. An adapter must therefore be stateless - everything
per-request arrives on `FetchContext`. Storing an access token on `self` would
leak it across users. `register()` evicts the cached instance so a swap takes
effect immediately, which is what makes the test fixture work.

`available()` is surfaced as the `available` field on `GET /api/connectors` and
is the field a UI must use when deciding whether to offer an app.
`POST /api/connectors/{key}/connect` enforces it with a **409**.

---

## 5. `UnavailableConnector`

The 52 catalogued apps with no integration resolve here.

```python
class UnavailableConnector(BaseConnector):
    """A catalogued app with no integration yet.

    Fails quiet on read and loud on write: a sweep over an unavailable app
    should not fill the log with errors, but an agent proposing an action
    against one is a bug worth surfacing.
    """
    def fetch(self, ctx):  return [], ctx.cursor
    def execute(self, action, payload, ctx):
        return ActionResult(ok=False,
            summary=f"{self.key} has no integration yet, so {action} cannot run.",
            error="no_adapter")
```

The asymmetry is the design. A sweep runs every 15 minutes over every connected
app; if unavailable apps logged an error each time, the log would be useless.
But an agent that proposes `send_email` against an app with no adapter has found
a real bug - either a catalog/registry drift or an agent routing to the wrong
connector - so that path returns `ok=False` with a distinguishable
`error="no_adapter"`.

Note this is reachable in practice only through a stale `Connection` row,
because `connect` now refuses unavailable apps outright.

---

## 5a. The scripted scenario is a test fixture

What used to be `connectors/demo.py` is now
`apps/api/tests/fixtures/scenario.py`, exporting `ScenarioConnector`. **It is
not importable from the product** - nothing under `duet/` references it.

`tests/conftest.py` installs it with an autouse fixture:

```python
SCENARIO_APPS = ("gmail", "slack", "google_calendar", "stripe", "quickbooks",
                 "gusto", "hubspot", "jira", "github", "docusign", "shopify",
                 "zendesk", "notion", "google_drive", "zoom", "linear",
                 "asana", "outlook")

@pytest.fixture(autouse=True)
def scenario_adapters():
    original = dict(registry.REAL)
    for key in SCENARIO_APPS:
        if key not in registry.REAL:
            registry.register(key, lambda k=key: ScenarioConnector(k))
    yield
    registry.REAL.clear()
    registry.REAL.update(original)
```

Two details worth copying if you write a similar fixture:

- **It saves and restores `REAL`.** `tests/test_live_connectors.py` needs the
  genuine adapters in place, so leaking the scenario registration across files
  would break it.
- **`if key not in registry.REAL`** means a real adapter wins. For `github`,
  `notion`, `linear`, `stripe` and `slack` the scenario does *not* override the
  live class - those apps are exercised by their own stubbed-network tests.

The old `DUET_FORCE_DEMO_CONNECTORS` escape hatch is gone. There is no runtime
switch that makes the product serve fake data.

---

## 5b. The six live adapters

`connectors/live/`. The package docstring states three rules every adapter in it
follows, and the first is the one that matters most:

> 1. **No credential, no work.** `fetch` returns `([], ctx.cursor)` and never
>    raises. An unconfigured connection is a normal state, not an error, and the
>    monitor sweeps every connection on a timer.

and the reason:

> if a credential is missing the adapter returns nothing rather than inventing a
> plausible-looking signal, because a fabricated signal in a briefing is worse
> than a silent connector.

Each one carries a hard-won detail in its docstring. These are worth reading
before writing the seventh, because they are the class of bug that scores
*correctly-wrong* and is invisible until a briefing says something false.

| Adapter | Auth | The detail that bit |
|---------|------|---------------------|
| `github_cli.py` | **none** - shells out to your `gh` CLI | Reuses an auth you already granted, so no token, no OAuth app, no setup step. Parsing works off `gh api` JSON, which is byte-identical to the REST API, so `_token_mode` is a clean seam for a server deployment. |
| `imap_mail.py` | app password | Opens the mailbox with **`EXAMINE` (readonly=True), not `SELECT`** - otherwise the server marks every fetched message `\Seen` and a proactive sweep silently reads the user's unread mail out from under them. |
| `slack.py` | bot token | **Slack answers HTTP 200 for errors.** A revoked token, a missing scope and a kicked-out bot all return `200 {"ok": false, ...}`. Checking `status_code` alone reports an empty-but-healthy workspace - *"the worst possible failure mode for a proactive assistant: it looks like silence."* Only DMs, explicit `<@U...>` mentions and `@here`/`@channel` broadcasts become signals. |
| `stripe.py` | API key | **Stripe counts in cents.** Every amount is divided by 100 before it reaches a hint, because `financial_factor` buckets at $1k/$5k/$20k/$50k and a missed division turns a $184 failure into a "$18,400 at stake" interrupt. `transfer_balance` never calls Stripe at all. |
| `linear.py` | API key | The key goes in `Authorization` **bare, with no `Bearer` prefix** - sending `Bearer` gets a 400 that reads like a malformed query. GraphQL also returns errors with HTTP 200 and an `errors` array, so the status code is never sufficient. |
| `notion.py` | integration token | Reads via `POST /search` sorted by `last_edited_time`, the only endpoint spanning every shared page without knowing the workspace layout. Deleting means archiving, so `delete_page` is reversible and ships a revert plan that un-archives. |

Two of the six need no OAuth app, which is why they are the two the README
suggests first: `github` (your `gh` login) and `imap` (an app password).

---

## 6. Adding the seventh connector

Worked end to end for Gmail. Five steps; the fifth is the one people forget.

### Step 1 - make sure the catalog entry says the truth

`gmail` is already in `catalog.py`. Before writing code, check that its
`write_scopes`, `signal_kinds` and `writes` match what you are about to
implement, and that each `WriteAction.risk` is one you would defend to a user:

```python
ConnectorSpec(
    key="gmail",
    read_scopes=("gmail.readonly", "gmail.metadata"),
    write_scopes=("gmail.compose", "gmail.send", "gmail.modify"),
    signal_kinds=("email", "thread_waiting", "vip_email"),
    writes=(
        WriteAction("create_draft",   "Save a draft",      _SAFE),
        WriteAction("send_email",     "Send an email",     _REVIEW,    reversible=False),
        WriteAction("archive",        "Archive a thread",  _REVIEW),
        WriteAction("delete_thread",  "Delete a thread",   _SENSITIVE, reversible=False),
    ),
    has_adapter=True,
)
```

`signal_kinds` must be keys the priority engine knows
(`priority.KIND_WEIGHTS`), or everything you emit scores the 8-point default.

### Step 2 - write the adapter

New file, `apps/api/duet/connectors/gmail.py`:

```python
"""Gmail adapter. Reads via users.messages.list, writes via users.messages.send."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from ..core.vault import get_vault
from .base import ActionResult, BaseConnector, FetchContext, RawSignal

API = "https://gmail.googleapis.com/gmail/v1"


class GmailConnector(BaseConnector):
    key = "gmail"

    # ---------------- read ----------------
    def fetch(self, ctx: FetchContext) -> tuple[list[RawSignal], dict[str, Any]]:
        if ctx.credential is None:
            return [], ctx.cursor

        def _call(cred: dict[str, Any]) -> tuple[list[RawSignal], dict[str, Any]]:
            headers = {"Authorization": f"Bearer {cred['access_token']}"}
            params: dict[str, Any] = {"maxResults": 50, "q": "is:unread in:inbox"}
            if token := ctx.cursor.get("page_token"):
                params["pageToken"] = token

            with httpx.Client(timeout=20.0) as http:
                listing = http.get(f"{API}/users/me/messages",
                                   headers=headers, params=params)
                listing.raise_for_status()
                body = listing.json()

                signals: list[RawSignal] = []
                for ref in body.get("messages", []):
                    detail = http.get(f"{API}/users/me/messages/{ref['id']}",
                                      headers=headers,
                                      params={"format": "metadata"})
                    detail.raise_for_status()
                    signals.append(self._to_signal(detail.json()))

            cursor = dict(ctx.cursor)
            cursor["page_token"] = body.get("nextPageToken")
            cursor["last_fetch"] = datetime.now(timezone.utc).isoformat()
            return signals, cursor

        # The secret exists only inside this call.
        return get_vault().inject(ctx.credential, _call)

    def _to_signal(self, msg: dict[str, Any]) -> RawSignal:
        headers = {h["name"].lower(): h["value"]
                   for h in msg.get("payload", {}).get("headers", [])}
        labels = set(msg.get("labelIds", []))
        sender = headers.get("from", "")

        # Classify, but do NOT decide. Hints are evidence; priority.py decides.
        hints: dict[str, Any] = {"external": "@" not in sender or True}
        if "list-unsubscribe" in headers:
            hints["list_unsubscribe"] = True
            hints["bulk"] = True
        if "CATEGORY_PROMOTIONS" in labels or "CATEGORY_SOCIAL" in labels:
            hints["bulk"] = True
        if headers.get("importance", "").lower() == "high":
            hints["importance"] = "high"

        kind = "email"
        if "IMPORTANT" in labels:
            kind = "vip_email"

        return RawSignal(
            external_id=msg["id"],            # stable vendor id → the dedupe key
            kind=kind,
            title=headers.get("subject", "(no subject)")[:500],
            body=msg.get("snippet", ""),
            actor=sender,
            url=f"https://mail.google.com/mail/u/0/#inbox/{msg['id']}",
            occurred_at=datetime.fromtimestamp(
                int(msg["internalDate"]) / 1000, tz=timezone.utc),
            raw={"thread_id": msg.get("threadId"), "labels": sorted(labels)},
            hints=hints,
        )

    # ---------------- write ----------------
    def execute(self, action: str, payload: dict[str, Any],
                ctx: FetchContext) -> ActionResult:
        if ctx.credential is None:
            return ActionResult(ok=False, summary="Gmail is not connected",
                                error="no_credential")

        handlers = {
            "create_draft":  self._create_draft,
            "send_email":    self._send_email,
            "archive":       self._archive,
            "delete_thread": self._delete_thread,
        }
        handler = handlers.get(action)
        if handler is None:
            # Fall through to BaseConnector: ok=False, error="not_implemented".
            return super().execute(action, payload, ctx)

        try:
            return get_vault().inject(ctx.credential,
                                      lambda cred: handler(cred, payload))
        except httpx.HTTPStatusError as exc:
            return ActionResult(
                ok=False,
                summary=f"Gmail refused the request ({exc.response.status_code})",
                error="vendor_error",
            )

    def _send_email(self, cred: dict[str, Any], payload: dict[str, Any]) -> ActionResult:
        import base64, email.message

        msg = email.message.EmailMessage()
        msg["To"] = payload["to"]
        msg["Subject"] = payload.get("subject", "")
        msg.set_content(payload.get("body", ""))
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

        with httpx.Client(timeout=20.0) as http:
            r = http.post(
                f"{API}/users/me/messages/send",
                headers={"Authorization": f"Bearer {cred['access_token']}"},
                json={"raw": raw},
            )
            r.raise_for_status()
            sent = r.json()

        return ActionResult(
            ok=True,
            summary=f"Email sent to {payload['to']}",   # the user reads this
            data={"id": sent["id"], "thread_id": sent.get("threadId")},
            external_id=sent["id"],
            revert_plan={},            # a sent email cannot be unsent. Say so.
        )

    def _archive(self, cred: dict[str, Any], payload: dict[str, Any]) -> ActionResult:
        thread_id = payload["thread_id"]
        with httpx.Client(timeout=20.0) as http:
            r = http.post(
                f"{API}/users/me/threads/{thread_id}/modify",
                headers={"Authorization": f"Bearer {cred['access_token']}"},
                json={"removeLabelIds": ["INBOX"]},
            )
            r.raise_for_status()
        return ActionResult(
            ok=True,
            summary="Thread archived",
            external_id=thread_id,
            # Self-describing: executor calls execute(plan["action"], plan, ctx).
            revert_plan={"action": "unarchive", "thread_id": thread_id},
        )

    # ... _create_draft, _delete_thread, _unarchive
```

Rules the adapter must hold to:

- **Never store a credential on `self`.** The registry caches one instance per
  key for the process lifetime, shared across users and threads.
- **Always go through `vault.inject()`.** Do not accept a raw credential
  argument - there should be no signature in your module that could be called
  with a plaintext secret.
- **`fetch()` must not write.** Not even a "mark as read". Reads happen
  unattended; that is the whole basis of the safety argument.
- **Supply `hints`, not verdicts.** Classify (`bulk`, `vip`, `amount`); do not
  score.
- **Use a stable `external_id`.** It is half of `uq_conn_external`. An id that
  changes between polls produces a duplicate signal every sweep.
- **`occurred_at` is vendor time.** Using ingest time breaks
  `urgency_factor`'s age arithmetic and makes a week-old email look fresh. For a
  `WAITING_ON_YOU` kind it is worse than cosmetic: ingest time makes a
  three-week-old review request look like it arrived this morning, so it loses
  the overdue bonus entirely.
- **Return `ActionResult(ok=False)`; do not raise.** The executor catches
  exceptions, but a caught exception becomes
  `summary="<TypeName>: <message>"` - which is what the user then reads in the
  thread.
- **Unknown action → `super().execute(...)`,** which fails closed.
- **Return an honest `revert_plan`.** `{}` for truly irreversible actions.
  Claiming reversibility you cannot deliver is worse than admitting you cannot.

### Step 3 - register it

Add it to `_register_live()`, which is the one place live adapters are wired:

```python
# connectors/registry.py
def _register_live() -> None:
    from .live import github_cli, gmail, imap_mail, linear, notion, slack, stripe
    ...
    register("gmail", gmail.GmailConnector)
```

Put the module in `connectors/live/`, alongside the other six.
`available("gmail")` and `is_live("gmail")` now return `True`,
`GET /api/connectors` reports `"available": true`, and `connect` stops
returning 409. **Nothing else in the system changes** - not the monitor, not
the orchestrator, not the executor, not the security agent.

### Step 4 - flip `has_adapter`

Set `has_adapter=True` in the `ConnectorSpec`, in the same commit.
`has_adapter` and `registry.live_keys()` are kept identical, and a drift
between them means the UI offers an app that cannot work.

### Step 5 - the OAuth flow, which does not exist yet

This is the step the current tree does not cover, and the reason "real
connector" is bigger than "write an adapter".

`POST /api/connectors/{key}/connect` takes a credential blob directly and puts
it in the vault. There is no authorize redirect, no code exchange, and **no
refresh**. For Gmail that means your stored `access_token` expires in an hour
and `fetch()` starts returning 401s, which the sweep records as
`conn.status = "error"` with the exception text in `conn.last_error`.

A real deployment needs:

1. `GET /api/connectors/{key}/authorize` → redirect to the provider with
   `state` bound to the user and the requested scopes.
2. `GET /api/oauth/callback` → exchange the code, call
   `vault.put(user.id, key, {"access_token", "refresh_token", "expires_at", ...})`,
   and set `Connection.scopes` **from the scopes the provider actually
   granted** - not from `spec.write_scopes`. Today `connect()` records what was
   requested, which means `security._scope_ok()` is checking an optimistic list.
3. Refresh-on-401 inside `vault.inject()`'s callback, writing the new token back
   with `vault.put()` (the ref is derived from `user_id:connector_key`, so a
   re-put overwrites in place).
4. Honour the provider's revocation endpoint in `disconnect()`, not just
   `vault.forget()`.

Until step 5 exists, an adapter registered in `REAL` works for as long as the
credential you pasted in stays valid.

### Testing an adapter without network access

`apps/api/tests/test_live_connectors.py` is the pattern to copy - 48 tests
across the six existing adapters, with the network stubbed per module. Its
docstring states what the tests are for:

> an adapter that mis-shapes a signal is invisible until a briefing says the
> wrong thing. The assertions that matter most are the hint ones - a Stripe
> amount off by 100x, an undetected Slack mention or a mojibake subject line all
> score correctly-wrong, and nothing downstream can recover from that.

No test there touches the network: `httpx` is replaced per module with a
`StubClient` that replays canned vendor responses **and records every request**,
which is what lets a test assert that a forbidden action made *no* call at all.

The useful assertions are the contract ones: `external_id` is stable across two
fetches of the same message, `occurred_at` is timezone-aware, `kind` is in
`KIND_WEIGHTS`, hint amounts are in the right units, and
`execute("nonsense", {}, ctx).ok is False`.

`test_agents.py::TestCatalogIntegrity` enforces several invariants across the
whole catalog, and `test_live_connectors.py::TestRegistration` checks the
registry wiring, so a malformed new `ConnectorSpec` or a missed
`register()` fails the suite rather than failing at runtime. Run
`cd apps/api && python -m pytest tests/ -q` after editing either.

---

## 7. Adding a connector that is not in the catalog

For an app with no `ConnectorSpec`, add the spec first. The catalog is the place
risk is declared, and `resolve()` will hand you a `BaseConnector` that reads
nothing until a key exists in `BY_KEY`.

Minimum viable entry:

```python
ConnectorSpec(
    key="my_app",
    name="My App",
    category="productivity",          # must be in CATEGORIES
    color="#336699",
    glyph="MA",
    summary="One sentence a user can act on when deciding whether to connect.",
    auth="api_key",
    read_scopes=("read",),
    write_scopes=("write",),
    signal_kinds=("doc_update",),     # must exist in priority.KIND_WEIGHTS
    writes=(
        WriteAction("create_thing", "Create a thing", _SAFE),
        WriteAction("delete_thing", "Delete a thing", _SENSITIVE,
                    reversible=False,
                    note="Permanently removes the record and its history."),
    ),
    monitored=True,
    has_adapter=False,                # until step 3
    tags=("example",),
)
```

If your app emits a kind of event the engine does not know, add it to
`priority.KIND_WEIGHTS` with a weight you can justify against the existing
scale - `payment_failed` is 30, a generic `email` is 8, a `doc_update` is 5.
Picking a number without reading the neighbours is how a priority engine stops
being trustworthy.

For a brand-new `category`, add it to `catalog.CATEGORIES` too, or the
connector gallery's category filter will not list it (`by_category()` still
works, but `GET /api/connectors` returns `CATEGORIES` as the UI's filter set).
