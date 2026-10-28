"""Runtime configuration. Local-first defaults: nothing here requires a cloud account."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("DUET_DATA_DIR", ROOT / ".data"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DUET_", env_file=".env", extra="ignore")

    # --- core ---
    app_name: str = "Claude Duet (local)"
    environment: str = "local"
    debug: bool = True

    # Postgres in compose, SQLite on a laptop. Same SQLAlchemy models either way.
    database_url: str = f"sqlite:///{(DATA_DIR / 'duet.db').as_posix()}"
    redis_url: str | None = None  # optional; falls back to the in-process event bus
    # Optional vector store. Unset means semantic recall uses the local hash
    # embedding in agents/memory.py, which is the laptop default.
    qdrant_url: str | None = None
    qdrant_collection: str = "duet_memory"

    # Execution sandbox: subprocess | docker. See core/sandbox.py - subprocess
    # is process isolation only; docker is the real boundary.
    sandbox_backend: str = "subprocess"

    # --- model layer ---
    # provider: auto | agent_cli | anthropic | ollama | openai | heuristic
    #
    # "auto" resolves at startup to the best thing actually present on this
    # machine (see resolve_provider): a signed-in agent CLI first, then an API
    # key, then a local model server, and only then the deterministic heuristic
    # planner. The point is that a fresh clone runs against a real model
    # without anyone configuring anything, whatever vendor they already use.
    #
    # llm_cli picks which agent CLI when several are installed:
    #   auto (default) | claude | codex | gemini | cursor | opencode
    llm_provider: str = "auto"
    llm_base_url: str = "http://localhost:11434"
    llm_cli: str = "auto"
    # Empty means "whatever that CLI defaults to", which is usually right -
    # a model name valid for one vendor is invalid for another.
    llm_model: str = ""
    llm_api_key: str | None = None
    llm_timeout_s: float = 120.0
    llm_temperature: float = 0.2

    # --- autonomy ---
    # How often the monitor loop sweeps connectors.
    #
    # Raise this rather than lower it, but for the right reason: scoring is pure
    # arithmetic, so a sweep costs no model turns. What it costs is one API call
    # per connected app per sweep - at 900s that is ~96 sweeps/day against every
    # vendor's rate limit, and 60s would be 1440. Model spend scales with tasks
    # you run, not with how often it looks.
    sweep_interval_s: int = 900
    briefing_hour: int = 7  # local hour for the morning brief
    # How much it does without asking.
    #   proportional - safe, reversible actions (drafts, notes, internal
    #                  records) just happen; anything that reaches a person,
    #                  moves money, changes access or deletes still waits.
    #   ask_everything - nothing at all happens without an explicit approval.
    # The forbidden tier is refused under both, with or without consent.
    autonomy: str = "proportional"
    max_parallel_tasks: int = 4
    task_retry_limit: int = 3

    # --- safety ---
    # Actions that can never be auto-approved, even with a standing grant.
    approval_expiry_minutes: int = 60 * 12
    secret_key: str = "dev-only-insecure-key-change-me"
    # Note: there is deliberately no "disable redaction" switch. Audit
    # scrubbing and the egress classifier are unconditional.

    # --- oauth clients ---
    # Set these in .env (or the real environment) after creating a client with
    # the provider. They are configuration, not user input: no client secret is
    # ever stored in the database or accepted from a request body.
    #
    # One client per provider covers every app that provider serves - a single
    # Google client authorises Gmail, Calendar, Drive and Sheets.
    google_client_id: str = ""
    google_client_secret: str = ""
    slack_client_id: str = ""
    slack_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    # Override when this API is reached on a different address than it binds -
    # behind a tunnel, say. Must match what is registered with the provider.
    oauth_redirect_base: str = ""

    # --- server ---
    host: str = "127.0.0.1"
    port: int = 8787
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return Settings()


settings = get_settings()
