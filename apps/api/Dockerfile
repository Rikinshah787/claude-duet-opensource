# syntax=docker/dockerfile:1
# -----------------------------------------------------------------------------
# Claude Duet API - FastAPI + uvicorn on :8787
# Build context is apps/api (see docker-compose.yml).
#   docker build -t duet-api apps/api
# -----------------------------------------------------------------------------

# ---------- stage 1: build the dependency venv ----------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# build-essential + libpq-dev only exist in this stage; the runtime image stays slim.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Dependency layer first so source edits do not invalidate the pip cache.
COPY requirements.txt ./
RUN pip install --upgrade pip \
 && pip install -r requirements.txt

# Production extras (Postgres driver, Redis, Qdrant, OTel). Set to 0 for a
# SQLite-only image:  docker build --build-arg INSTALL_PROD_EXTRAS=0 ...
ARG INSTALL_PROD_EXTRAS=1
RUN if [ "$INSTALL_PROD_EXTRAS" = "1" ]; then \
      pip install "psycopg[binary]>=3.2" "redis>=5.2"; \
    fi

# ---------- stage 2: runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DUET_HOST=0.0.0.0 \
    DUET_PORT=8787 \
    DUET_DATA_DIR=/app/.data

# curl is used by HEALTHCHECK and is handy for debugging a running container.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Non-root user. Fixed uid/gid so bind-mounted volumes have predictable ownership.
RUN groupadd --gid 10001 duet \
 && useradd --uid 10001 --gid duet --create-home --shell /usr/sbin/nologin duet

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Source last: everything above is cached across code changes.
COPY --chown=duet:duet duet ./duet
COPY --chown=duet:duet pyproject.toml requirements.txt ./

# Writable data dir for the SQLite file, WAL sidecars and the credential vault.
# There is no apps/api/.dockerignore, so host __pycache__ dirs can ride along in
# the COPY above - drop them rather than shipping stale bytecode.
RUN find /app/duet -name '__pycache__' -type d -prune -exec rm -rf {} + \
 && mkdir -p /app/.data \
 && chown -R duet:duet /app

USER duet

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8787/api/health || exit 1

CMD ["uvicorn","duet.main:app","--host","0.0.0.0","--port","8787"]
