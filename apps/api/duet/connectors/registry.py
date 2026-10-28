"""Resolves a connector key to a runnable adapter.

There is no fake adapter here. An app in the catalog without a real integration
resolves to `UnavailableConnector`, which returns nothing and says why - it does
not invent email, invoices or tickets. The catalog is a statement of what the
product intends to support; `available()` is the statement of what works today,
and the UI shows the difference rather than blurring it.

Tests substitute their own adapters through `register()`; see
`tests/fixtures/scenario.py`.
"""
from __future__ import annotations

import logging
from typing import Any

from .base import ActionResult, BaseConnector, FetchContext, RawSignal
from .catalog import BY_KEY

log = logging.getLogger("duet.connectors")


class UnavailableConnector(BaseConnector):
    """A catalogued app with no integration yet.

    Fails quiet on read and loud on write: a sweep over an unavailable app
    should not fill the log with errors, but an agent proposing an action
    against one is a bug worth surfacing.
    """

    def __init__(self, key: str):
        self.key = key

    def fetch(self, ctx: FetchContext) -> tuple[list[RawSignal], dict[str, Any]]:
        return [], ctx.cursor

    def execute(self, action: str, payload: dict[str, Any], ctx: FetchContext) -> ActionResult:
        return ActionResult(
            ok=False,
            summary=f"{self.key} has no integration yet, so {action} cannot run.",
            error="no_adapter",
        )


# Real adapters, keyed by connector. Populated by `_register_live()` so that one
# adapter failing to import (a missing dependency, a syntax error) disables only
# that app instead of taking the API down at startup.
REAL: dict[str, type[BaseConnector]] = {}

_cache: dict[str, BaseConnector] = {}


def register(connector_key: str, adapter_cls: type[BaseConnector]) -> None:
    """Add or replace an adapter. Used by live registration and by tests."""
    REAL[connector_key] = adapter_cls
    _cache.pop(connector_key, None)


def _register_live() -> None:
    from .live import gmail, github_cli, imap_mail, linear, notion, slack, stripe

    register("github", github_cli.GitHubConnector)
    register("gmail", gmail.GmailConnector)
    register("notion", notion.NotionConnector)
    register("linear", linear.LinearConnector)
    register("stripe", stripe.StripeConnector)
    register("slack", slack.SlackConnector)
    register("imap", imap_mail.ImapConnector)


try:
    _register_live()
except Exception as exc:  # pragma: no cover - defensive
    log.warning("some live adapters failed to load: %s", exc)


def resolve(connector_key: str) -> BaseConnector:
    if connector_key in _cache:
        return _cache[connector_key]

    if connector_key in REAL:
        adapter: BaseConnector = REAL[connector_key]()
    else:
        adapter = UnavailableConnector(connector_key)

    _cache[connector_key] = adapter
    return adapter


def is_live(connector_key: str) -> bool:
    """True when a real integration exists for this app."""
    return connector_key in REAL


def available(connector_key: str) -> bool:
    """True when this app can actually be connected and used today."""
    return connector_key in REAL and connector_key in BY_KEY


def live_keys() -> list[str]:
    return sorted(REAL)


def reset_cache() -> None:
    _cache.clear()
