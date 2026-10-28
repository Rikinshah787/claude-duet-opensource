# 08 - Deployment

Four ways to run Duet, in increasing order of moving parts:

1. [Local, zero dependencies](#1-local-zero-dependencies) - SQLite, heuristic provider.
2. [Docker Compose](#2-docker-compose) - Postgres, Redis, Qdrant, API, web.
3. [Kubernetes](#3-kubernetes) - plain manifests under `deploy/k8s/`.
4. [Air-gapped / self-hosted](#4-air-gapped-and-self-hosted) - no outbound network at all.

Every setting is a field on `Settings` in `apps/api/duet/config.py` and is read
with the `DUET_` prefix. `.env.example` at the repo root documents all of them;
the full list is in [section 5](#5-configuration-reference).

---

## 1. Local, zero dependencies

Requirements: Python 3.12+, and Node 22+ only if you want the web UI.

**No database server, no broker, no model.** SQLite at
`apps/api/.data/duet.db`, Fernet vault at `apps/api/.data/vault.enc`, and the
`heuristic` LLM provider.

### One command

```powershell
# Windows PowerShell
.\scripts\dev.ps1
```

```bash
# Linux / macOS / WSL / Git Bash
./scripts/dev.sh
# or
make dev
```

Both scripts do the same five things: create `.venv` if missing, install
`apps/api/requirements.txt`, start `uvicorn duet.main:app --reload` in the
background, poll `http://127.0.0.1:8787/api/health` until it answers (40s
default), `npm install` only when `node_modules` is absent, then run
`npm run dev` in the foreground. Ctrl+C stops both.

Useful flags:

| PowerShell | bash | Effect |
|------------|------|--------|
| `-ApiPort 9000` | `API_PORT=9000` | move the API |
| `-HealthTimeoutSeconds 60` | `HEALTH_TIMEOUT=60` | slower machines |
| `-SkipWeb` | `SKIP_WEB=1` | API only; tails the API log |
| `-SkipInstall` | `SKIP_INSTALL=1` | skip pip |

API logs go to `.data/dev-api.log`. If the health poll times out, the script
prints the last 40 lines of that log plus the four usual causes. On Windows,
`.\scripts\stop.ps1` cleans up anything left running (it matches on this repo's
path, so an unrelated uvicorn or Next.js project on the same machine is left
alone; `-WhatIf` shows the plan).

### By hand

```powershell
# Windows PowerShell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r apps\api\requirements.txt
cd apps\api
..\..\.venv\Scripts\python.exe -m uvicorn duet.main:app --host 127.0.0.1 --port 8787 --reload
```

```bash
# bash
python3.12 -m venv .venv
./.venv/bin/python -m pip install -r apps/api/requirements.txt
cd apps/api
../../.venv/bin/python -m uvicorn duet.main:app --host 127.0.0.1 --port 8787 --reload
```

`python -m duet.main` also works - `main.py::run()` calls `uvicorn.run()` with
`settings.host` / `settings.port` / `reload=settings.debug`.

Then the web app, in a second terminal:

```bash
cd apps/web
npm install
npm run dev          # http://localhost:3000
```

`apps/web/next.config.mjs` rewrites `/api/*` to `NEXT_PUBLIC_API_BASE`
(default `http://127.0.0.1:8787`), so the browser talks to one origin in dev.

### Connect something real

There is no demo data to load and no seed endpoint - the product ships with no
fake signals at all. Connect one of the six working apps instead. The two that
need no OAuth app:

```bash
API=http://127.0.0.1:8787

# GitHub: no token. Uses the gh CLI you are already signed in to.
gh auth status
curl -s -X POST $API/api/connectors/github/connect   -H 'Content-Type: application/json' -d '{"grant_write": false}'

# IMAP mail: an app password is enough.
curl -s -X POST $API/api/connectors/imap/connect   -H 'Content-Type: application/json'   -d '{"credential": {"host": "imap.gmail.com", "user": "you@example.com",
                      "password": "<app password>"}}'

curl -s -X POST $API/api/sync          # or: make sync
```

The other four live apps (`linear`, `notion`, `slack`, `stripe`) take an API key
or integration token in `credential`. The remaining 52 catalogued apps return
`409` from `connect` until an adapter exists. See
[docs/05-connectors.md](05-connectors.md) and
[docs/04-api.md](04-api.md#10-syncing-and-maintenance).

### What is created on disk

| Path | What | Safe to delete? |
|------|------|-----------------|
| `apps/api/.data/duet.db` (+ `-wal`, `-shm`) | everything | yes - recreated by `init_db()` |
| `apps/api/.data/vault.enc` | encrypted credentials | yes, but you re-enter every credential |
| `.data/dev-api.log` | dev launcher's API log | yes |
| `.venv/` | Python environment | yes |

`make clean` removes the database and caches and **leaves `vault.enc` and
`.venv` in place** - deliberately, since the vault is the only thing that is
genuinely expensive to recreate.

### Working without an `.env`

`pydantic-settings` reads `.env` **relative to the process working
directory**, which for uvicorn is `apps/api`. The repo-root `.env` is read by
Docker Compose, not by a bare-metal uvicorn. If you run locally and want
overrides, copy the template to `apps/api/.env`:

```bash
cp .env.example apps/api/.env
```

Everything has a working default, so this is optional - except
`DUET_SECRET_KEY`, which defaults to `dev-only-insecure-key-change-me`. That is
fine for a laptop you are the only user of and **not fine** for anything else.

---

## 2. Docker Compose

`docker-compose.yml` at the repo root. Five services; one is behind a profile.

| Service | Image | Port | Role |
|---------|-------|------|------|
| `postgres` | `postgres:16-alpine` | 5432 | durable store |
| `redis` | `redis:7-alpine` | 6379 | optional event bus / cache |
| `qdrant` | `qdrant/qdrant:latest` | 6333 / 6334 | optional vector store |
| `api` | built from `apps/api/Dockerfile` | 8787 | FastAPI + the scheduler |
| `web` | built from `apps/web/Dockerfile` | 3000 | Next.js |
| `ollama` | `ollama/ollama:latest` | 11434 | **profile `local-llm`**, off by default |

### Run it

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste as DUET_SECRET_KEY

docker compose up -d --build
# or
make up
```

Compose **refuses to start the API** without a secret key -
`DUET_SECRET_KEY: ${DUET_SECRET_KEY:?set DUET_SECRET_KEY in .env - it encrypts
the credential vault}`. That is the one guard rail bare metal does not have.

```
Web    http://localhost:3000
API    http://localhost:8787
Health http://localhost:8787/api/health
Qdrant http://localhost:6333/dashboard
```

```bash
make logs                    # docker compose logs -f --tail=100
docker compose logs -f api
make down                    # volumes kept
docker compose down -v       # volumes dropped too
```

### What Compose changes versus local

| | Local | Compose |
|---|-------|---------|
| `DUET_DATABASE_URL` | SQLite | `postgresql+psycopg://duet:duet@postgres:5432/duet` |
| `DUET_REDIS_URL` | unset | `redis://redis:6379/0` |
| `DUET_QDRANT_URL` | unset | `http://qdrant:6333` |
| `DUET_HOST` | `127.0.0.1` | `0.0.0.0` |
| `DUET_DEBUG` | `true` | `false` |
| `DUET_ENVIRONMENT` | `local` | `docker` |
| `DUET_DATA_DIR` | `apps/api/.data` | `/app/.data` on the `api_data` volume |

Two of those are reserved rather than wired: **nothing in the code reads
`DUET_REDIS_URL` or `DUET_QDRANT_URL` yet.** Both are `Settings` fields
(`redis_url`, `qdrant_url`, plus `qdrant_collection`) and both are
unreferenced everywhere else. Running those two containers today costs memory
and buys nothing. Both are roadmap items in
[docs/09-roadmap.md](09-roadmap.md); until then, deleting them from your compose
file is a reasonable thing to do.

### The API image

`apps/api/Dockerfile`, two stages:

- **builder** - `python:3.12-slim`, installs `build-essential` and `libpq-dev`,
  creates `/opt/venv`, installs `requirements.txt`. Then, under
  `ARG INSTALL_PROD_EXTRAS=1`, also installs `psycopg[binary]` and `redis`.
  Build with `--build-arg INSTALL_PROD_EXTRAS=0` for a SQLite-only image.
- **runtime** - `python:3.12-slim`, copies `/opt/venv`, runs as a non-root user
  (`duet`, fixed uid/gid 10001 so bind-mounted volumes have predictable
  ownership), writable `/app/.data`, and a `HEALTHCHECK` curling
  `/api/health` every 30s with a 20s start period.

The `psycopg` driver only exists because `DUET_DATABASE_URL` uses the
`postgresql+psycopg://` dialect. If you point it at a plain `postgresql://` URL
SQLAlchemy will look for `psycopg2`, which is not installed.

The Postgres service has a `pg_isready` healthcheck and the API `depends_on` it
with `condition: service_healthy` - because `init_db()` runs at startup and
*"without it the first migration races an empty cluster."*

### Volumes

`postgres_data`, `redis_data`, `qdrant_data`, `api_data`, `ollama_models`.
`api_data` is the one that matters: it holds `vault.enc`. Dropping it with
`down -v` destroys every stored credential, and `DUET_SECRET_KEY` cannot
recover them.

### The web image and `NEXT_PUBLIC_API_BASE`

`next.config.mjs` inlines `NEXT_PUBLIC_API_BASE` **at build time**, so Compose
passes it both as a build arg and as a runtime env var. The runtime value is
`http://localhost:8787` - **not** `http://api:8787` - because that URL is
resolved by the browser on the host, not inside the container network.

Changing the public hostname means a rebuild, not a restart:

```bash
NEXT_PUBLIC_API_BASE=https://duet.example.com docker compose up -d --build web
```

### Adding a local model

```bash
docker compose --profile local-llm up -d ollama
docker compose exec ollama ollama pull qwen2.5:7b-instruct
```

Then set in `.env` and restart the API:

```ini
DUET_LLM_PROVIDER=ollama
DUET_LLM_BASE_URL=http://ollama:11434
DUET_LLM_MODEL=qwen2.5:7b-instruct
```

```bash
docker compose up -d api
curl -s http://localhost:8787/api/health | grep -o '"llm":{[^}]*}'
```

`llm.detail` distinguishes the failure modes: `"ollama: qwen2.5:7b-instruct"`
(good), `"ollama is up but 'qwen2.5:7b-instruct' is not pulled"` (pull it), or
`"ollama unreachable: ConnectError"` (wrong URL or not started).

Model files land in the `ollama_models` volume and survive `down` but not
`down -v`. GPU users: uncomment the `deploy.resources.reservations.devices`
block on the `ollama` service.

---

## 3. Kubernetes

Plain manifests in `deploy/k8s/` - no Helm, no Kustomize. Everything lands in
the `claude-duet` namespace. `deploy/k8s/README.md` is the operational
walkthrough; this section is the architectural context.

```
deploy/k8s/
  namespace.yaml           the claude-duet namespace
  configmap.yaml           duet-config: every non-secret DUET_* var
  secret.example.yaml      template - copy to secret.yaml, fill in, keep out of git
  postgres.yaml            StatefulSet + headless Service
  redis.yaml               optional; emptyDir, no persistence
  qdrant.yaml              optional vector store
  api-deployment.yaml      Service + PVC + Deployment (1 replica)
  web-deployment.yaml      Service + Deployment (2 replicas)
  ingress.yaml             one host, /api/* → api, / → web
```

```bash
kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f deploy/k8s/configmap.yaml
kubectl apply -f deploy/k8s/secret.yaml        # your filled-in copy
kubectl apply -f deploy/k8s/                   # the rest; kubectl sorts by kind

kubectl -n claude-duet rollout status deploy/duet-api
kubectl -n claude-duet port-forward svc/duet-api 8787:8787
curl -fsS http://127.0.0.1:8787/api/health
```

### Why the API is pinned to one replica

This is the most important constraint in the whole document, and it has **two
independent causes**:

**1. The vault is a file on a ReadWriteOnce PVC.** `vault.enc` lives at
`/app/.data/vault.enc` on `duet-api-data`, which is `ReadWriteOnce`. The
Deployment therefore uses `strategy: Recreate` rather than `RollingUpdate` -
two pods cannot mount it simultaneously. Scaling out needs either a
ReadWriteMany volume or the vault moved into a real secret manager, which
`core/vault.py` is explicitly designed as the seam for.

**2. The scheduler runs in-process.** Every API pod starts its own
`Scheduler` (`main.py` lifespan). Two replicas means:

- two sweeps per interval, doubling connector calls and model cost;
- two briefing loops, mitigated by the `Briefing` existence check but racy;
- **a non-atomic task claim.** `scheduler._run_queued_tasks()` re-reads the
  status in a second session, which narrows the window but does not close it.
  Two pods can run the same task - and a task that reaches the executor twice
  is a duplicate send.

**The second cause is the real blocker,** and it is not fixed by a
ReadWriteMany volume. To scale the API horizontally you must first either:

- run the scheduler as a **separate single-replica Deployment** with
  `DUET_DISABLE_SCHEDULER=1` on the API pods - the smallest correct change,
  and the one the env var exists for; or
- make the task claim atomic (`SELECT … FOR UPDATE SKIP LOCKED` on Postgres)
  and make `executor.decide()` idempotent, then run N schedulers safely.

See [docs/07-workflows.md](07-workflows.md#7-in-process-now-temporal-later).

`duet-web` is stateless and runs 2 replicas.

### Secrets

`secret.example.yaml` is a template. The `README` in that directory is blunt
about the consequence:

> `DUET_SECRET_KEY` encrypts the credential vault at `/app/.data/vault.enc`.
> Lose or rotate it and the existing vault **cannot** be decrypted - every
> connector credential has to be re-entered. Back it up separately from the
> volume.

`DUET_DATABASE_URL` is in the Secret, not the ConfigMap, because it carries the
Postgres password.

### Hostname is a build-time concern

`NEXT_PUBLIC_API_BASE` is inlined into the browser bundle by `next.config.mjs`.
Changing it in `configmap.yaml` after the image is built does nothing. Rebuild
`duet-web` with the right `--build-arg`, and set `DUET_CORS_ORIGINS` in the
ConfigMap and `spec.rules[0].host` in `ingress.yaml` to match.

### What the manifests deliberately omit

No HPA (see the replica constraint), no PodDisruptionBudget, no NetworkPolicy,
no ServiceMonitor, no `storageClassName` (cluster default), and TLS is commented
out pending cert-manager. Redis uses `emptyDir` with no AOF or RDB, which is
honest given nothing reads it yet.

All pods run `runAsNonRoot` with `allowPrivilegeEscalation: false` and all
capabilities dropped. `readOnlyRootFilesystem` is `false` for the API and Qdrant
because both need writable data directories.

### Things to add before calling it production

| Gap | Why it matters |
|-----|----------------|
| Schema migrations | `init_db()` is `create_all` - additive only. There is no Alembic, so a column change means dropping data. Add Alembic and run it as an init container or a Job. |
| Authentication | `X-Duet-User` is an unauthenticated header. Put an identity-aware proxy in front, or implement the token claim in `api/deps.py::current_user`. See [docs/06-security.md](06-security.md). |
| Vault in a secret manager | A file on a PVC, encrypted with an env-var key, blocks horizontal scaling and leaks on a memory dump. |
| Database backups | `vault.enc` and the Postgres volume, backed up separately - the vault is useless without its key, and the key must not live next to it. |
| NetworkPolicy | The API only needs egress to Postgres, the model server and the vendor APIs. Default-allow egress on a service that holds OAuth tokens is a poor default. |
| Rate limiting at the edge | There is none in the application. |
| Resource limits tuned | Model-backed requests can run to `DUET_LLM_TIMEOUT_S` (120s), and `ingress.yaml` already raises `proxy-read-timeout` to 180s to match. |

---

## 4. Air-gapped and self-hosted

Duet is designed to run with no outbound internet. Nothing in the default
configuration calls a hosted service.

### Why it works offline

| Subsystem | Offline behaviour |
|-----------|-------------------|
| Priority engine | pure Python arithmetic - `core/priority.py` |
| Sweep, briefing, approvals, audit, vault | pure Python + the database |
| Connectors | the 6 live adapters need network and return `([], cursor)` without a credential; the other 52 resolve to `UnavailableConnector`, which also returns nothing. Nothing fabricates data, so an offline install is simply quiet |
| Embeddings | `agents/memory.py::embed()` is a `blake2b` hashing projection - no model, no network |
| LLM | `heuristic` makes zero network calls. **`auto` does not guarantee that** - it prefers an agent CLI, which calls its vendor. Pin `DUET_LLM_PROVIDER=heuristic` or `ollama` in a sealed enclave |

The `heuristic` provider is the point. From `llm/client.py`:

> The heuristic provider is not a toy. It is what makes this product
> installable: the sweep, the scoring, the briefing and the approval gate are
> all deterministic code, so the system does useful work before anyone
> downloads a 7B model.

### Checklist for a sealed environment

1. **Pre-stage the Python wheels.** `pip download -r apps/api/requirements.txt
   -d wheels/`, then `pip install --no-index --find-links wheels/ -r
   apps/api/requirements.txt` inside the enclave.
2. **Pre-stage `node_modules`** or run an internal npm registry.
   `package-lock.json` is committed.
3. **Pre-stage the container images** if you use Compose or Kubernetes:
   `postgres:16-alpine`, `redis:7-alpine`, `qdrant/qdrant`,
   `python:3.12-slim`, `node` (for the web build), `ollama/ollama`.
4. **Generate `DUET_SECRET_KEY` inside the enclave.** Back it up separately
   from `vault.enc`, and never over the boundary.
5. **Leave `DUET_LLM_PROVIDER=heuristic`** or point it at an in-enclave model
   server.
6. **Set `DUET_CORS_ORIGINS`** to the real internal hostname.
7. **Pin the provider.** Do not leave `DUET_LLM_PROVIDER=auto` in a sealed
   enclave: `auto` prefers a signed-in agent CLI, and those call their vendor's
   API. Set `heuristic` (no network at all) or `ollama` with an in-enclave
   server.
8. **Decide your connector egress policy before connecting anything.** A live
   adapter is, by definition, outbound network access to a vendor. Of the six,
   `imap` is the one that can plausibly stay inside a perimeter - a company
   Dovecot box speaks IMAP and needs no internet. `github`, `linear`, `notion`,
   `slack` and `stripe` all reach a SaaS endpoint, so in a true air gap they
   will sit connected and silent, which is the correct failure mode but not a
   useful product.
9. **There is nothing to demonstrate with.** The product ships no sample data,
   so an air-gapped install with no reachable connector shows an empty, honest
   UI. If you need a demo, drive the API directly or run the test suite, which
   carries the scripted scenario as a fixture.

### Choosing a model backend

Five provider values, and the default is `auto`, which resolves at startup to
whatever the machine has: **agent CLI → `DUET_LLM_API_KEY` (anthropic) → local
Ollama → heuristic**. The ordering is by setup cost, not quality - see
[docs/04-api.md](04-api.md#12-the-model-layer-at-runtime).

**An agent CLI you already pay for** needs no configuration at all. If `claude`,
`codex`, `gemini`, `cursor-agent` or `opencode` is on `PATH` and signed in,
`auto` finds it. `_locate()` also probes a few conventional install paths
(`~/.local/bin`, `%APPDATA%
pm`) for CLIs not on `PATH`.

```ini
DUET_LLM_CLI=codex        # only needed when several are installed
```

For an air-gapped box this is often the wrong choice - these CLIs call their
vendor's API - so for a sealed enclave use `ollama` or `heuristic` below.

**Ollama**, outside Docker:

```bash
ollama serve                              # 11434
ollama pull qwen2.5:7b-instruct
```

```ini
DUET_LLM_PROVIDER=ollama
DUET_LLM_BASE_URL=http://localhost:11434
DUET_LLM_MODEL=qwen2.5:7b-instruct
DUET_LLM_TIMEOUT_S=120
```

The client posts to `/api/chat` with `stream: false` and reads token counts
from `prompt_eval_count` / `eval_count`, which is what populates
`Task.tokens_in` / `tokens_out`.

**Any OpenAI-compatible server** - vLLM, LM Studio, llama.cpp's server,
LocalAI, or a hosted API:

```ini
DUET_LLM_PROVIDER=openai
DUET_LLM_BASE_URL=http://localhost:8000      # no trailing /v1; the client adds it
DUET_LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
DUET_LLM_API_KEY=                            # sent as "Authorization: Bearer" when set
```

The client posts to `/v1/chat/completions` and probes `/v1/models` for
`available()`. Note `openai` is **not** in the `auto` chain - there is no way to
probe for it without knowing the URL first - so it must be named explicitly.

**The Claude API directly**, when you have a key and want lower latency than
process startup allows:

```ini
DUET_LLM_PROVIDER=anthropic
DUET_LLM_API_KEY=sk-ant-...
DUET_LLM_MODEL=                              # empty is fine
```

This is the one provider `auto` will pick on the strength of an env var alone,
which is worth knowing if you set `DUET_LLM_API_KEY` for something else.

**Choosing a model.** The two model-backed call sites are
`PlannerAgent` (JSON plan) and `CommunicationAgent._draft()` (JSON
`{subject, body}`), plus the briefing headline. Both go through
`complete_json()`, which appends *"Respond with JSON only. No prose, no code
fences"* and then parses defensively - `_extract_json()` strips fences and, as a
last resort, finds the outermost `{}` or `[]` in the string. So:

- An instruction-tuned model that can hold a JSON format is the requirement.
  A base/completion model will mostly fail `_validate()` and fall back to the
  heuristic plan, which looks like "the model isn't doing anything".
- Small models are fine. `qwen2.5:7b-instruct` is the default for a reason: the
  decomposition task is narrow and the output is parsed, not read.
- `DUET_LLM_TEMPERATURE` defaults to `0.2`, and `complete_json()` overrides it
  to `0.1`. Low, *"because planner output is parsed, not read."*
- Keep `DUET_LLM_TIMEOUT_S` high on CPU-only inference. A 7B model on CPU can
  take 30-60 seconds for a plan, and that blocks the task-runner loop for the
  duration.

**Failure is non-fatal, by design.** `LLMClient.complete()` wraps every provider
call:

```python
except Exception as exc:    # a dead model server must not take the app down
    log.warning("llm provider %s failed (%s); falling back", self.provider, exc)
return Completion(text=fallback if fallback is not None else "",
                  provider="heuristic", degraded=self.provider != "heuristic")
```

A stopped Ollama means the planner silently uses keyword routing and drafts come
out as templates. `GET /api/health` is where you notice, and
`Completion.degraded` is the flag that marks it - though nothing currently
surfaces `degraded` to the user, which is a small gap worth closing.

---

## 5. Configuration reference

Every field on `Settings` (`apps/api/duet/config.py`), read with the `DUET_`
prefix. Unknown `DUET_*` variables are ignored (`extra="ignore"`).

| Variable | Default | Read by |
|----------|---------|---------|
| `DUET_APP_NAME` | `Claude Duet (local)` | display only |
| `DUET_ENVIRONMENT` | `local` | labelling only |
| `DUET_DEBUG` | `true` | log level; `main.run()`'s `reload` |
| `DUET_DATABASE_URL` | `sqlite:///<DATA_DIR>/duet.db` | `db.py::_make_engine` |
| `DUET_REDIS_URL` | `None` | **nothing** |
| `DUET_QDRANT_URL` | `None` | **nothing** - reserved for vector recall |
| `DUET_QDRANT_COLLECTION` | `duet_memory` | **nothing** |
| `DUET_SANDBOX_BACKEND` | `subprocess` | `core/sandbox.py`. `subprocess` is process isolation only; `docker` is the real boundary |
| `DUET_LLM_PROVIDER` | **`auto`** | `llm/client.py::resolve_provider`. `auto` \| `agent_cli` \| `anthropic` \| `ollama` \| `openai` \| `heuristic` |
| `DUET_LLM_CLI` | `auto` | `llm/agent_cli.py::pick`. `auto` \| `claude` \| `codex` \| `gemini` \| `cursor` \| `opencode`. Naming one that is not installed fails loudly |
| `DUET_LLM_BASE_URL` | `http://localhost:11434` | `llm/client.py` - Ollama probe and the `openai` endpoint |
| `DUET_LLM_MODEL` | **`""` (empty)** | `llm/client.py`. Empty means that CLI's or server's own default |
| `DUET_LLM_API_KEY` | `None` | `llm/client.py` - the `anthropic` provider, and a Bearer token for `openai` |
| `DUET_LLM_TIMEOUT_S` | `120.0` | `llm/client.py` |
| `DUET_LLM_TEMPERATURE` | `0.2` | `llm/client.py` |
| `DUET_SWEEP_INTERVAL_S` | `900` | `scheduler._tick` |
| `DUET_BRIEFING_HOUR` | `7` | `scheduler._maybe_brief` |
| `DUET_MAX_PARALLEL_TASKS` | `4` | `scheduler._run_queued_tasks` (batch size) |
| `DUET_TASK_RETRY_LIMIT` | `3` | `orchestrator.run_task`, `scheduler._housekeeping` |
| `DUET_APPROVAL_EXPIRY_MINUTES` | `720` | `orchestrator._create_approvals` |
| `DUET_SECRET_KEY` | `dev-only-insecure-key-change-me` | `core/vault.py` - **change this** |
| `DUET_HOST` | `127.0.0.1` | `main.run()` |
| `DUET_PORT` | `8787` | `main.run()` |
| `DUET_CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | `main.py` CORS middleware |

There is deliberately **no setting to disable redaction or the egress
classifier.** A `redact_sensitive` flag existed and was read nowhere; it was
removed rather than wired, and `config.py` now says scrubbing and the classifier
are unconditional.

> **`.env.example` is currently out of date** and will mislead you if you copy
> it verbatim. It still sets `DUET_LLM_PROVIDER=heuristic` (the code default is
> now `auto`) and `DUET_LLM_MODEL=qwen2.5:7b-instruct` (now empty by default),
> and it does not mention `DUET_LLM_CLI`, `DUET_QDRANT_COLLECTION` or
> `DUET_SANDBOX_BACKEND`. It also still lists `DUET_REDACT_SENSITIVE`, which is
> silently ignored (`extra="ignore"`). **The table above is derived from
> `config.py` and is authoritative.** Setting `DUET_LLM_PROVIDER=heuristic`
> from the template is the practical trap: it pins you to the offline planner
> even though you have a usable agent CLI signed in.

Per-user settings, not environment variables: `quiet_hours`, `timezone`,
`briefing_optin` and `finance` (`starting_cash`, `monthly_burn`,
`ar_collection_rate`) live on the `users` row and are set through
`PATCH /api/settings`. Until `finance` is populated the executive dashboard
returns no runway projection rather than a fabricated one.

Read from the **process environment at import time**, not as `Settings` fields:

| Variable | Default | Effect |
|----------|---------|--------|
| `DUET_DATA_DIR` | `apps/api/.data` | database + vault location. Must be set before the process starts |
| `DUET_VAULT_PATH` | `<DATA_DIR>/vault.enc` | vault file location |
| `DUET_DISABLE_SCHEDULER` | unset | `1`/`true`/`yes` stops the autonomous loop. **Set this in tests** |

Not read by the API at all - used by Compose or the web build:

`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_PORT`,
`REDIS_PORT`, `QDRANT_PORT`, `NEXT_PUBLIC_API_BASE`, `WEB_PORT`,
`OLLAMA_PORT`, `OLLAMA_KEEP_ALIVE`.

---

## 6. Operating it

### Health

```bash
curl -s http://127.0.0.1:8787/api/health
```

```json
{"status": "ok", "version": "0.1.0", "db": true,
 "llm": {"ok": true, "detail": "heuristic (no model server)", "provider": "heuristic"},
 "scheduler": {"running": true, "sweep_interval_s": 900, "next_sweep_in_s": 412,
               "briefing_hour": 7, "tasks_run": 3, "sweeps": 2, "briefings": 1,
               "errors": 0, "last_error": null}}
```

`status` is `degraded` when the database query fails. `scheduler.errors` and
`last_error` accumulate from the tick's catch-all, so a non-zero `errors` with
`running: true` means the loop survived something you should read the log about.

This is the Docker `HEALTHCHECK` endpoint and the readiness gate used by both
dev launchers.

### Metrics

```bash
curl -s http://127.0.0.1:8787/api/metrics             # JSON
curl -s http://127.0.0.1:8787/api/metrics/prometheus  # text/plain gauges
```

The four numbers to watch, in order:

| Metric | Healthy | Means |
|--------|---------|-------|
| `connectors.errored` | `[]` | a listed connector is one the sweep is blind to |
| `signals.noise_ratio` | high | share of observations filtered out. Trending to zero means the briefing is becoming an inbox |
| `approvals.oldest_pending_age_h` | low | an approval nobody answered will be expired by housekeeping |
| `cost.estimated_spend_usd` | — | model spend so far. `cost.token_cost_driver` is `"tasks"` - sweeps cost vendor API calls, not model turns, so raising the interval does not reduce this |

Also `security.injections_detected`, which counts audit rows with
`action="security.injection_detected"`. See
[docs/06-security.md](06-security.md#7a-c15-prompt-injection-screening).

Note `/api/metrics/prometheus` is **user-scoped**, so a cluster scrape without
an `X-Duet-User` header reports the owner's numbers rather than the
installation's. Fine single-tenant; wrong the moment it is not.

OpenTelemetry is separate and optional: `main.py` calls
`telemetry.instrument(app)` in the lifespan hook, which attaches
`FastAPIInstrumentor` plus an OTLP span exporter **only when the SDK is
installed** (`pip install -e ".[prod]"`), and logs `"tracing exported via
OTLP"` when it engages. Without the SDK it is a silent no-op, which is what
keeps the local install dependency-free.

### Sandbox capability

```bash
curl -s http://127.0.0.1:8787/api/agents | python -c '
import sys, json; print(json.load(sys.stdin)["sandbox"])'
# {"subprocess": true, "docker": true, "browser": false, "active": "subprocess",
#  "note": "subprocess is process isolation, not a security boundary - ..."}
```

`docker` and `browser` are probed at request time. For the browser backend:

```bash
pip install playwright && playwright install chromium
```

Set `DUET_SANDBOX_BACKEND=docker` for anything that is not your own laptop -
the default backend is process isolation, not a security boundary, and
`available()` says so in its own payload.

### Logs

`main.py` configures the root logger: `DEBUG` when `settings.debug`, else
`INFO`, format `%(asctime)s %(levelname)-7s %(name)s: %(message)s`. Named
loggers: `duet`, `duet.scheduler`, `duet.orchestrator`, `duet.executor`,
`duet.monitor`, `duet.llm`, `duet.vault`.

Unhandled request exceptions are logged with a traceback and returned to the
client as `{"detail": "<TypeName>. Check the server log."}` - *"Never leak a
stack trace to the client; always leave one in the log."*

### Verifying integrity

```bash
curl -s http://127.0.0.1:8787/api/audit/verify
# {"valid": true, "checked": 41, "head": "9f2c1b7e4a0d83f1"}
```

Worth running after any database maintenance. A `valid: false` with a
`broken_at` id is either tampering or a failed write that left the chain
inconsistent.

### Backups

Two things, and they must be stored **separately**:

1. **The database.** SQLite: copy `duet.db`, `duet.db-wal` and `duet.db-shm`
   together, or better, `sqlite3 duet.db ".backup out.db"` while running.
   Postgres: `pg_dump`.
2. **`vault.enc` plus `DUET_SECRET_KEY`.** The file is useless without the key,
   and the key is useless without the file. Backing them up to the same place
   defeats the encryption.

There is no credential rotation path. A compromised `DUET_SECRET_KEY` means
re-entering every connector credential.

### Resetting

```bash
curl -X POST http://127.0.0.1:8787/api/admin/reset  # this account, incl. credentials
make clean                                          # drop the local SQLite file
docker compose down -v                              # drop every volume
```

`POST /api/admin/reset` erases this user's signals, tasks, approvals, messages,
memories and connections, **and destroys their vault credentials** - it reports
the count as `deleted.credentials`. It keeps the `User` row, `PolicyRule` rows
and the audit chain; the `admin.reset` row in that chain is the record the
erasure happened. Not reversible, and deliberately not in the UI header. See
[docs/04-api.md](04-api.md#10-syncing-and-maintenance).

### Quality gates

```bash
make lint      # ruff check + ruff format --check on the API, tsc --noEmit on the web app
make test      # pytest
```

`pyproject.toml` configures ruff (line length 100, target py312, rules
`E,F,I,UP,B,C4,SIM` with `B008` and `E501` ignored) and pytest
(`testpaths=["tests"]`, `asyncio_mode="auto"`, `-q --strict-markers`).

**196 tests, all passing**, across six files in `apps/api/tests/`:

```bash
cd apps/api
../../.venv/Scripts/python.exe -m pytest tests/ -q     # Windows
../../.venv/bin/python -m pytest tests/ -q             # POSIX
```

| File | Tests | Covers |
|------|-------|--------|
| `test_live_connectors.py` | 48 | the 6 live adapters, network stubbed |
| `test_priority.py` | 38 | scoring, quiet hours, waiting-on-you ageing, learning, explainability, sweep |
| `test_safety.py` | 35 | egress gate, redaction, Security agent, approval gate, audit chain, vault, review regressions |
| `test_agents.py` | 33 | catalog integrity, planner routing, registry, orchestrator trace, memory |
| `test_injection.py` | 27 | detection, sanitisation, framing, ingest pipeline, hostile signals leaving the working set |
| `test_sandbox.py` | 15 | workspace traversal, execution, isolation, capability reporting |

The suite is still growing - run it rather than trusting those numbers. It takes
roughly two minutes; `test_live_connectors.py` is the slow part.

`tests/conftest.py` handles the environment for you: an isolated SQLite file and
vault per run in a temp directory, schema dropped and recreated per test,
`DUET_LLM_PROVIDER=heuristic`, and `DUET_DISABLE_SCHEDULER=1` - *"an autonomous
background loop would make assertions race."* If you write a test outside this
suite that imports `duet.main`, set that variable yourself.

The control-to-test map is in
[docs/06-security.md](06-security.md#10-testing-the-security-model).

Note the suite emits a `PytestConfigWarning: Unknown config option:
asyncio_mode` - `pytest-asyncio` is declared in the `dev` extra but is not
installed in `.venv`. Harmless today (no async tests), and `make install` pulls
it in.
