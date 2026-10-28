# Claude Duet

A local-first, proactive AI executive assistant. It watches your connected apps
on a schedule, scores everything it finds against an explainable priority model,
and decides whether to interrupt you, save it for the morning brief, or stay
quiet. When you ask it to do something it plans the work across eight
specialist agents, and every action that would change the world outside the
process stops at a human approval - nothing is sent, paid, signed or deleted
without you clicking yes.

Runs on a laptop with no cloud account and no API key. **Bring your own agent
subscription** - if you already pay for Claude Code, OpenAI Codex, Gemini CLI,
Cursor or OpenCode, Duet drives whichever one you have signed in, so there is no
second subscription and no billing setup. SQLite, an encrypted credential vault
on disk, and a deterministic planner that still works when you have none of
them.

---

## What it does

**Notices things.** A background sweep polls every connected app and scores each
observation on eight dimensions - the kind of thing it is, deadline proximity,
money at stake, who it came from, explicit importance, noise markers, your own
rules, and what you have historically done with that kind of thing. The score
decides one of four dispositions: interrupt now, save for the brief, defer, or
ignore. Thresholds are 72 / 40 / 18, and during your quiet hours the
interrupt bar rises to 88 - only a genuine emergency gets through at 02:00.

**Explains itself.** Every factor returns points *and* a sentence, so "why are
you telling me about this?" has a real answer:

```
Invoice #2291 - Castle Retail - $18,400 - 41 days overdue        score 71
  kind 27.6 · urgency 16.4 · financial 13.0 · importance 8.0 · preference 18.0
  "41 days overdue; $18,400 at stake; you boosted 'invoice'"
```

**Rations interruption.** Even when five things clear the notify bar you get one
grouped message, not five. A burst of notifications is how a proactive
assistant teaches you to ignore it.

**Writes a morning brief.** One report, led by what you have to *decide* rather
than what is merely new. Sections - Needs your approval, Money, Customers, Your
day, Waiting on you, Work, What I'd do first - are assembled deterministically
from scored signals. The model, if you have one, writes only the two-sentence
opener; selection stays in code because selection is the part that has to be
auditable.

**Plans multi-step work.** Ask *"get me ready for the Harbor Ventures partner
meeting"* and the planner decomposes it: research the attendees and open
threads, pull the numbers they will ask about, then draft a one-page prep note.
Common shapes are matched by regex and cost no model turn; the model earns its
place on the open-ended asks.

**Holds every consequential action.** Agents can only `propose()`. A proposal
passes the Security agent's four checks - policy floor, connected-app scope,
outbound-content screening, risk tier - and becomes an approval request showing
you exactly what will happen. One module in the entire codebase can call a
third-party write API.

**Refuses some things outright.** Moving money between accounts, sending
payments, trading securities, changing MFA and rotating root credentials are
blocked by policy in two independent places. There is no consent flag that
unlocks them, because the most likely way such an instruction arrives is as
something that *looks* like it came from you.

**Screens what it reads.** Content from a connected app is written by third
parties and may be addressed at the assistant rather than at you. Eight weighted
patterns catch override attempts, role hijacks, secrecy demands, exfiltration
instructions and fake authority claims; invisible and bidi-override characters
are stripped. Screening runs at ingest, where a hit is flagged on the signal and
written to the audit chain; again at prompt assembly, where untrusted text is
delimited and labelled with its source and hostile payloads are withheld
entirely; and once more at the working-set boundary, where a hostile signal is
removed from what the agents can see at all - so a phishing email's subject
cannot rank as a relevant finding and its sender can never become the recipient
of a reply.

**Won't invent numbers.** Set your cash position and burn rate and you get a
runway projection with its assumptions attached. Leave them unset and you get
no projection and a note saying so, because an invented starting balance renders
identically to a real one and an operator would reasonably act on it.

**Never shows the model a credential.** Agents hold an opaque
`CredentialHandle`. The secret is resolved inside `vault.inject()` at the moment
an approved call is made and is never returned up the stack, logged, or placed
anywhere an agent's context can reach.

**Proves what it did.** Every sweep, score, plan, approval decision, execution
and revert is a row in a SHA-256 hash-chained audit log. Edit or delete a row
and `GET /api/audit/verify` tells you which one.

**Remembers conservatively.** Four memory kinds with different lifetimes. Only
explicit preference statements ("never send anything to an investor without
asking me") are promoted to long-term memory - an assistant that over-remembers
is one that acts on a throwaway remark from six weeks ago.

**Shows you the cost.** `GET /api/metrics` reports per-agent latency and error
rates, how much of what it saw it chose *not* to surface, the age of the oldest
unanswered approval, connector health, injections detected, and projected
monthly token spend at your current sweep cadence. Prometheus format at
`/api/metrics/prometheus`.

The web UI has eight views: Thread, Briefing, Tasks, Approvals, Apps,
Dashboard, Activity, Settings.

---

## 60-second quickstart

### Windows PowerShell

```powershell
git clone <repo> claude-duet
cd claude-duet
.\scripts\dev.ps1
```

### bash (Linux / macOS / WSL / Git Bash)

```bash
git clone <repo> claude-duet
cd claude-duet
./scripts/dev.sh        # or: make dev
```

Both create `.venv`, install the API dependencies, start uvicorn on
`127.0.0.1:8787`, wait for `/api/health`, run `npm install` only if
`node_modules` is missing, then start the Next.js dev server in the foreground.
Ctrl+C stops both.

```
Web    http://localhost:3000
API    http://127.0.0.1:8787
Docs   http://127.0.0.1:8787/docs
Health http://127.0.0.1:8787/api/health
```

Requirements: Python 3.12+, and Node 22+ only if you want the UI. Nothing else.

Useful flags: `-SkipWeb` / `SKIP_WEB=1` for API only, `-ApiPort 9000` /
`API_PORT=9000` to move the port, `-HealthTimeoutSeconds 60` /
`HEALTH_TIMEOUT=60` on a slow machine. On Windows, `.\scripts\stop.ps1` cleans
up anything left running.

### By hand

```powershell
# Windows PowerShell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r apps\api\requirements.txt
cd apps\api
..\..\.venv\Scripts\python.exe -m uvicorn duet.main:app --port 8787 --reload
```

```bash
# bash
python3.12 -m venv .venv
./.venv/bin/python -m pip install -r apps/api/requirements.txt
cd apps/api
../../.venv/bin/python -m uvicorn duet.main:app --port 8787 --reload
```

### Docker Compose

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste as DUET_SECRET_KEY
docker compose up -d --build        # or: make up
```

Postgres + Redis + Qdrant + API + web. Compose refuses to start without
`DUET_SECRET_KEY`, because that key encrypts your credential vault - lose it and
the vault cannot be decrypted.

---

## First run

There is no demo data and no seed button. You connect a real app and it reads
your real account. The fastest two to switch on:

**GitHub** needs no token at all - it shells out to the `gh` CLI you are already
signed in to:

```bash
gh auth status                      # must already be logged in
API=http://127.0.0.1:8787
curl -s -X POST $API/api/connectors/github/connect \
  -H 'Content-Type: application/json' -d '{"grant_write": false}'
```

**Mail** over IMAP works with an app password - no OAuth app to register:

```bash
curl -s -X POST $API/api/connectors/imap/connect \
  -H 'Content-Type: application/json' \
  -d '{"credential": {"host": "imap.gmail.com", "user": "you@example.com",
                      "password": "<app password>"}}'
```

Then:

```bash
# 1. look at everything connected, now
curl -s -X POST $API/api/sync | python -m json.tool
# or one app:  curl -s -X POST $API/api/connectors/github/sync

# 2. see what it decided needs you now versus later
curl -s "$API/api/signals?disposition=notify_now" | python -m json.tool
curl -s "$API/api/signals?disposition=briefing&limit=10" | python -m json.tool

# 3. the morning report
curl -s -X POST $API/api/briefing/generate | python -m json.tool

# 4. ask it something - a question gets a prose answer, not a task receipt
curl -s -X POST $API/api/thread -H 'Content-Type: application/json' \
  -d '{"text":"what has been waiting on me longest?"}' | python -m json.tool

# 5. ask for work, then approve it
curl -s -X POST $API/api/thread -H 'Content-Type: application/json' \
  -d '{"text":"draft replies to the PRs waiting on my review"}' | python -m json.tool

curl -s "$API/api/approvals?status=pending" | python -m json.tool
curl -s -X POST $API/api/approvals/<id>/approve \
  -H 'Content-Type: application/json' -d '{"note":"go ahead"}' | python -m json.tool

# prove nothing was tampered with
curl -s $API/api/audit/verify | python -m json.tool
```

`make sync` is the shorthand for step 1. Connecting an app that is catalogued
but not yet implemented returns **409** rather than pretending:

```
"Slack is in the catalog but has no integration yet. Connecting it would
 look like it works and never produce anything."
```

To start over - this destroys stored credentials and disconnects every app, and
deliberately leaves the audit chain intact as the record that it happened:

```bash
curl -s -X POST $API/api/admin/reset | python -m json.tool
```

In the UI: **Apps** → connect one, then **Thread**, **Briefing**,
**Approvals**, **Activity**.

---

## Bring your own agent subscription

**You almost certainly already pay for a model this can use.** The default
provider is `auto`, and on startup it resolves to whichever coding-agent CLI you
have signed in - no API key, no second subscription, no billing setup. Vendor
choice is yours:

| CLI | `DUET_LLM_CLI` | Binary |
|-----|----------------|--------|
| Claude Code | `claude` | `claude` |
| OpenAI Codex CLI | `codex` | `codex` |
| Gemini CLI | `gemini` | `gemini` |
| Cursor Agent | `cursor` | `cursor-agent` |
| OpenCode | `opencode` | `opencode` |

Nothing to configure if you have exactly one installed. With several, name the
one you want:

```ini
DUET_LLM_CLI=codex          # auto (default) | claude | codex | gemini | cursor | opencode
```

Asking for one you do not have **fails loudly** rather than silently
substituting another - `"codex CLI not found (installed: Claude Code)"`.

```bash
curl -s http://127.0.0.1:8787/api/health
# "llm": {"ok": true, "detail": "Claude Code (/home/you/.local/bin/claude)",
#         "provider": "agent_cli"}
```

Adding a vendor is a row in the `SPECS` table in `apps/api/duet/llm/agent_cli.py` -
each entry says how to invoke that CLI headlessly, how to pass a system prompt
and how to select a model. Not code.

### The other four providers

`auto` resolution order is **agent CLI → `DUET_LLM_API_KEY` → local Ollama →
heuristic**, ordered by how little setup each demands rather than by quality.
Override it by naming one:

```ini
DUET_LLM_PROVIDER=anthropic     # the Claude API directly, lower latency than process startup
DUET_LLM_API_KEY=sk-ant-...

DUET_LLM_PROVIDER=ollama        # local models
DUET_LLM_BASE_URL=http://localhost:11434
DUET_LLM_MODEL=qwen2.5:7b-instruct

DUET_LLM_PROVIDER=openai        # any OpenAI-compatible server: vLLM, LM Studio,
DUET_LLM_BASE_URL=http://localhost:8000   # llama.cpp, LocalAI, or a hosted API
DUET_LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
```

`openai` is reachable only by naming it - it is not in the `auto` chain, because
there is no way to probe for it without knowing the URL first.

**`DUET_LLM_MODEL` defaults to empty**, meaning "whatever that CLI or server
defaults to". That is usually right: a model name valid for one vendor is
invalid for another.

### Running with no model at all

`heuristic` is the floor, not a broken state. The sweep, the scoring, the
briefing, the approval gate and the audit chain are deterministic code, so the
product does useful work before you install anything. The model adds fluent
drafts, prose answers and open-ended decomposition.

A dead CLI or model server is non-fatal by design: drafts fall back to
templates, planning falls back to keyword routing, and `Completion.degraded`
marks it. For Ollama or OpenAI, use an **instruction-tuned** model - both call
sites ask for JSON that is parsed rather than read, and a base model mostly
fails validation, which looks like the model doing nothing.

---

## What works today vs. what is only catalogued

**There is no simulated mode.** The shipped package contains no fake data at
all - no demo adapter, no scripted scenario, no seed endpoint. The scenario that
used to ship as `connectors/demo.py` now lives in
`apps/api/tests/fixtures/scenario.py` as a test-only fixture, registered by an
autouse fixture in `conftest.py` and never importable from the product.

### 7 apps work today

`github`, `gmail`, `imap`, `linear`, `notion`, `slack`, `stripe` - real adapters
under `apps/api/duet/connectors/live/`, talking to real vendor APIs over the
network.

```bash
cd apps/api && python -c "from duet.connectors import registry; print(registry.live_keys())"
# ['github', 'gmail', 'imap', 'linear', 'notion', 'slack', 'stripe']
```

They reach their credentials three different ways, and the difference is
visible in the UI rather than papered over. **Gmail** signs in with Google and
holds a refreshing OAuth grant. **GitHub** shells out to the `gh` CLI you are
already signed in to and reads no stored credential at all. **IMAP**, Slack,
Notion, Linear and Stripe take a token you paste. `catalog.has_adapter` means
exactly "a real integration exists" and is identical to that list.

### 51 are catalogued but not implemented, and the product says so

An app in the catalog with no integration resolves to `UnavailableConnector`,
which **never invents data**: `fetch()` returns `([], cursor)` and `execute()`
returns `ok=False, error="no_adapter"`. Quiet on read so a sweep does not fill
the log, loud on write because an agent proposing an action against an
unavailable app is a bug worth surfacing.

You cannot even connect one. `POST /api/connectors/{key}/connect` returns
**409**, and the message is the design rationale: *"Connecting it would look
like it works and never produce anything."* `GET /api/connectors` carries
`available: boolean` next to `has_adapter` so the UI shows the difference rather
than blurring it.

The catalog is a statement of intent - what the product means to support, with
each app's risk tiers already declared. `available()` is the statement of what
works. Those are deliberately two different things.

### Genuinely not wired

- **`core/sandbox.py`** - three isolation backends (subprocess / docker /
  browser) with an environment denylist, traversal rejection and an honest
  capability probe, covered by 15 tests, but **no caller on the task path**. It
  is tested library infrastructure exposed through `GET /api/agents`, not a
  wired capability, and the `1password.use_login` catalog note describes a flow
  that does not yet exist.
- **OAuth covers Google and Slack, not the rest.** The flow is real for those
  two providers (see below); every other connector still takes a pasted token,
  and for those `Connection.scopes` records what was *requested* rather than
  what a provider granted.
- **The cash runway is not fabricated, but it is not sourced either.**
  `AnalyticsAgent._runway()` reads your own figures from `User.finance` and
  returns `configured: false` with no series when they are unset, rather than
  projecting from invented constants - *"an invented starting balance renders
  identically to a real one, and an operator would reasonably act on it."*
  Nothing reads a balance from a connected account yet.

### Real, and running

| Subsystem | What runs |

| Subsystem | What runs |
|-----------|-----------|
| **Connectors** | 7 live adapters against real vendor APIs; 51 catalogued and honestly reported as unavailable |
| **OAuth** | Authorization-code flow with PKCE for Google and Slack - single-use CSRF state, refresh before expiry, one grant shared across a provider's connectors |
| **Model layer** | 5 providers - 5 agent CLIs through one `SPECS` table, the Claude API, Ollama, any OpenAI-compatible server, and the offline heuristic planner |
| **Priority engine** | 8 factors, ~35 signal kinds, 12 connector biases, 4 thresholds, quiet hours with midnight wrap, per-factor explanations, and age that *increases* urgency for the 9 kinds where someone is waiting on you |
| **Proactive sweep** | A real scheduler thread on a 900s cadence. Read-only by construction, per-connection error isolation, dedupe enforced by a database constraint |
| **Briefing** | Deterministic section assembly, pending-approval promotion, derived recommendations, stored so a brief you read last week still says what it said |
| **Planning** | 3 composite patterns, 5 routes, a validated model path with a five-agent allowlist |
| **8 agents** | Planner, research, communication, productivity, analytics, automation, security, memory - all dispatching for real, with per-step persistence and per-agent exception isolation |
| **Approvals** | Real rows with a 12-hour expiry, a real executor with five execution-time re-checks, real revert support, real expiry sweep |
| **Security gate** | Forbidden-action floor (enforced twice), scope checks that fail closed, two-tier egress classifier, user `never` rules |
| **Injection defence** | 8 weighted patterns, hostile threshold, invisible-character sanitiser. Screens at ingest (audited), at prompt assembly (hostile payloads withheld), and at the working-set boundary (hostile signals removed from what agents can see) |
| **Vault** | Fernet-encrypted on disk, handle-only access, no getter, destroyed on disconnect |
| **Audit** | SHA-256 hash chain over 17 action types, with real verification |
| **Memory** | 96-dim hashing embedding, salience + recency ranking, conservative preference capture, behaviour history feeding back into scoring |
| **API** | 38 REST routes, FastAPI, OpenAPI at `/docs` |
| **Telemetry** | Per-agent stats, noise ratio, approval age, connector health, cost attribution, Prometheus export, optional OpenTelemetry |
| **Tests** | 196 passing, across safety, priority, agents, injection, sandbox and the 6 live adapters |

**The architecture is the product.** Adding the seventh app means implementing
`fetch()` / `execute()` against the same protocol and one
`register("key", Cls)` call. Nothing above the connector layer changes - not the
scoring, not the planner, not the security gate, not the executor. The six that
exist are the proof, and
[docs/05-connectors.md](docs/05-connectors.md#6-adding-a-real-connector) walks
through writing the next one.

The honest summary: **six apps work against your real accounts today. The other
52 are catalogued, and the product tells you so instead of pretending.**

---

## Signing in, and what holds the keys

Gmail connects by signing in to Google. You click **Sign in with Google**, approve
the scopes on Google's own consent screen, and the grant lands in the vault —
no password is typed into Duet and you can revoke it from your Google account
at any time.

```
POST /api/oauth/gmail/start   -> { url }        authorize URL, PKCE challenge stored
GET  /api/oauth/callback      <- code + state   exchanged server-side for tokens
GET  /api/oauth/gmail/status  -> { configured, required, scopes, redirect_uri }
```

What the flow actually guarantees:

- **PKCE (S256)** on every authorization, and a single-use `state` with a 600s
  TTL, so a replayed or forged callback is refused.
- **`access_type=offline` + `prompt=consent`**, because without both Google
  omits the refresh token and the connection silently dies in an hour.
- **One grant per provider.** Gmail, Calendar, Drive and Sheets share a single
  Google authorization rather than asking four times.
- **A token with no expiry is treated as non-expiring**, not as expiring in an
  hour — Slack bot tokens and GitHub OAuth tokens have no `expires_in`, and
  defaulting them to 3600s would have broken them on the hour.

This needs an OAuth client of your own — your Google Cloud project, your
consent screen. That part cannot be done for you, so the UI shows the exact
redirect URI to register and the two environment variables to set, and reports
`configured: false` until they are present.

**The credential is never handed to a model.** The vault stores a single
Fernet-encrypted blob and gives out opaque `CredentialHandle` references;
`vault.inject(handle, fn)` resolves the secret only inside `fn`, and
`access_token()` refreshes in place and never returns the refresh token. Agents
hold references, not secrets.

> `DUET_SECRET_KEY` encrypts the vault. Lose it and the vault is
> undecryptable — every connection has to be re-authorized. Changing it does not
> error; it reads back as empty.

**Connectors that only work with OAuth say so.** The Gmail adapter speaks to the
Gmail API with a grant and nothing else, so an app password pasted there would be
accepted by the vault and then fail on first use — a connection that reports
success and reads no mail. Those connectors are marked `required: true` and the
token form is not offered at all.

## Repo layout

```
claude-duet/
├── apps/
│   ├── api/                      Python 3.12 · FastAPI · SQLAlchemy 2
│   │   ├── duet/
│   │   │   ├── main.py           app factory, CORS, lifespan, scheduler start
│   │   │   ├── config.py         pydantic-settings, DUET_ prefix
│   │   │   ├── db.py             engine, SessionLocal, session_scope, init_db
│   │   │   ├── models.py         13 tables, 5 enums - the whole domain
│   │   │   ├── api/              thread · work · apps · system + deps (RBAC)
│   │   │   ├── core/
│   │   │   │   ├── scheduler.py      the autonomous loop - 4 cadences
│   │   │   │   ├── monitor.py        the proactive sweep (read-only)
│   │   │   │   ├── priority.py       the priority engine
│   │   │   │   ├── briefing.py       the morning brief
│   │   │   │   ├── orchestrator.py   the task pipeline
│   │   │   │   ├── executor.py       THE ONLY WRITER
│   │   │   │   ├── injection.py      prompt-injection screening
│   │   │   │   ├── redaction.py      the egress gate
│   │   │   │   ├── vault.py          credential isolation
│   │   │   │   ├── sandbox.py        isolated execution
│   │   │   │   ├── audit.py          hash-chained log
│   │   │   │   └── telemetry.py      observability
│   │   │   ├── agents/           base · planner · workers · security · memory
│   │   │   ├── connectors/
│   │   │   │   ├── base.py           the Connector protocol
│   │   │   │   ├── catalog.py        58 specs, 6 with real integrations
│   │   │   │   ├── registry.py       resolve → live adapter | Unavailable
│   │   │   │   └── live/             github_cli · imap_mail · linear ·
│   │   │   │                         notion · slack · stripe
│   │   │   └── llm/
│   │   │       ├── agent_cli.py      5 agent CLIs behind one SPECS table
│   │   │       └── client.py         agent_cli | anthropic | ollama |
│   │   │                             openai | heuristic
│   │   ├── tests/               196 tests - safety · priority · agents ·
│   │   │   └── fixtures/        injection · sandbox · live_connectors
│   │   │                        scenario.py: the scripted week, test-only
│   │   ├── Dockerfile            two-stage, non-root, healthcheck
│   │   ├── pyproject.toml        deps, ruff, pytest
│   │   └── requirements.txt
│   └── web/                      Next.js 15 · React 19 · Tailwind · TanStack Query
│       ├── app/                  layout · page · providers
│       ├── components/           AppShell · HeaderBar · nav · views/
│       └── lib/                  api · hooks · types · format
├── docs/                         the documentation suite (below)
├── deploy/k8s/                   plain manifests, no Helm
├── scripts/                      dev.ps1 · dev.sh · stop.ps1
├── docker-compose.yml
├── Makefile                      install · dev · api · web · test · lint · sync · up
└── .env.example                  every setting, documented
```

---

## Documentation

| Doc | Contents |
|-----|----------|
| [docs/01-architecture.md](docs/01-architecture.md) | Component diagram, the layering rules, the request path for a user task, the proactive path for a sweep, and a step-by-step walkthrough of one real task from message to approval |
| [docs/02-data-model.md](docs/02-data-model.md) | Every table, column, index and constraint, an ASCII ERD, why `uq_conn_external` is the sweep's dedupe, and the full lifecycles of a Signal and an Approval |
| [docs/03-agents.md](docs/03-agents.md) | The eight agents, the exact `ROUTES` and `COMPOSITES` dispatch rules, the `AgentContext`/`AgentResult`/`Proposal` contract, and how to add a ninth |
| [docs/04-api.md](docs/04-api.md) | All 39 endpoints with request bodies, response shapes and status codes, plus curl walkthroughs of the main flows |
| [docs/05-connectors.md](docs/05-connectors.md) | The `Connector` protocol, `RawSignal`/`ActionResult`/`CredentialHandle`/`FetchContext`, the full 57-entry catalog with risk tiers, and a step-by-step guide to writing a real adapter |
| [docs/06-security.md](docs/06-security.md) | Threat model, 17 controls each mapped to its implementing file *and the test that pins it*, a "what an attacker would try and what stops them" table, and the known gaps |
| [docs/07-workflows.md](docs/07-workflows.md) | The scheduler's four loops, retries and durability, why the sweep cadence is minutes not seconds, event-driven triggers, and what a Temporal migration changes |
| [docs/08-deployment.md](docs/08-deployment.md) | Local, Compose, Kubernetes, air-gapped, running a local model, the full configuration reference, and how to operate it |
| [docs/09-roadmap.md](docs/09-roadmap.md) | What ships today, the MVP gap, phased effort estimates, where production actually breaks, and a cost model for the sweep cadence |

New to the codebase? Read `apps/api/duet/models.py`, then
`apps/api/duet/core/priority.py`, then
[docs/01-architecture.md](docs/01-architecture.md).

---

## Testing

**196 tests, all passing.** The safety invariants are the load-bearing part of
the repo, so they are pinned by name rather than by comment.

```bash
cd apps/api
../../.venv/Scripts/python.exe -m pytest tests/ -q     # Windows
../../.venv/bin/python -m pytest tests/ -q             # POSIX
# or, from the repo root:
make test
```

| File | Tests | Covers |
|------|-------|--------|
| `tests/test_live_connectors.py` | 48 | the 6 live adapters with the network stubbed - signal shape, hint correctness, forbidden actions making no call |
| `tests/test_priority.py` | 38 | noise suppression, consequence surfacing, quiet hours, waiting-on-you ageing, user rules, learning, explainability, sweep |
| `tests/test_safety.py` | 35 | egress gate, redaction, Security agent, approval gate, audit chain, vault, review regressions |
| `tests/test_agents.py` | 33 | catalog integrity, planner routing, registry, orchestrator trace, memory |
| `tests/test_injection.py` | 27 | detection, sanitisation, framing, ingest pipeline, hostile signals leaving the working set |
| `tests/test_sandbox.py` | 15 | workspace traversal, execution, isolation, capability reporting |

The three that matter most, because they test the claims this README makes:

- **`test_safety.py::TestApprovalGate::test_task_with_a_send_does_not_send`** -
  runs a complete task that genuinely wants to send an email, then asserts the
  world outside the process is unchanged. This is the test for "agents can only
  propose".
- **`test_injection.py::TestIngestPipeline::test_injection_cannot_reach_a_side_effect`** -
  takes the seeded phishing email with its real injected payload, runs it
  through the real pipeline, and asserts the injected instruction produced no
  execution. This is the test for containment.
- **`test_injection.py::TestHostileSignalsLeaveTheWorkingSet::test_reply_is_never_addressed_to_a_flagged_sender`** -
  the one that caught a real bug. Detection had worked and the payload had been
  withheld, and the assistant still drafted a reminder addressed to the
  attacker, because the phishing email's *subject* made it rank as a relevant
  finding. Hostile signals are now removed from the agents' working set
  entirely, metadata and all.

Also worth knowing about: `TestEgressGate::test_card_number_never_leaves_even_when_asked`
(the "even when asked" is the whole point of the STRICT tier),
`TestAuditChain::test_tampering_is_detected`,
`TestVault::test_handle_never_reveals_the_secret`, and
`TestIsolation::test_host_secrets_are_not_visible`, which parametrises over the
sandbox's whole environment denylist.

`tests/conftest.py` handles isolation for you - a fresh SQLite file and vault
per run in a temp directory, schema dropped and recreated per test, the
heuristic LLM provider, and `DUET_DISABLE_SCHEDULER=1`, because an autonomous
background loop would make assertions race. Set that variable yourself for any
test outside this suite that imports `duet.main`.

It also supplies the scripted scenario, which is the **only** place fake data
exists. An autouse `scenario_adapters` fixture registers
`fixtures/scenario.py::ScenarioConnector` over the 18 apps the scenario covers,
then restores `registry.REAL` afterwards - the save-and-restore matters, because
`test_live_connectors.py` needs the genuine adapters in place. The product
cannot reach any of it.

A full control-to-test map is in
[docs/06-security.md](docs/06-security.md#10-testing-the-security-model).

---

## Development

```bash
make install    # .venv + API deps + web deps
make dev        # API + web together
make api        # API only, with --reload
make web        # Next.js dev server only
make test       # pytest
make lint       # ruff check + format --check, then tsc --noEmit
make sync       # POST /api/sync - check every connected app now
make up         # docker compose up -d --build
make clean      # caches, build output and the local database (keeps vault.enc)
```

---

## Two things to know before you trust it with real accounts

1. **`DUET_SECRET_KEY` defaults to `dev-only-insecure-key-change-me`.** It
   encrypts the credential vault. Docker Compose refuses to start without a real
   one; a bare-metal run silently uses the default. Generate one with
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`, and back it
   up somewhere other than next to `vault.enc`. There is no rotation path.
2. **There is no authentication.** `X-Duet-User` is an unauthenticated header,
   so once a second user exists any caller can act as either. The read
   endpoints are scoped per user, but nothing proves who the caller is.
   Local-first and single-tenant is the design, and binding to `127.0.0.1` is
   what keeps it honest. Do not expose the port without an identity-aware proxy
   in front. Both gaps, and the rest, are listed in
   [docs/06-security.md](docs/06-security.md#9-known-gaps).

---

## License

MIT.
