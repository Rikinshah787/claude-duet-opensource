"""Test fixtures.

Each test gets an isolated SQLite file and its own vault, so nothing leaks
between tests and the suite can run in any order. The scheduler is disabled -
an autonomous background loop would make assertions race.

The product itself ships no fake data: an app without a real integration
resolves to `UnavailableConnector` and returns nothing. So the suite supplies
its own world by registering `ScenarioConnector` for the apps it wants to
exercise. That keeps the determinism tests need without putting invented email
into the shipped package, and it means a test can never accidentally pass
because of a mock that real users also run.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

os.environ["DUET_DISABLE_SCHEDULER"] = "1"
# Never let a test shell out to a real agent CLI or hit a vendor API.
os.environ["DUET_LLM_PROVIDER"] = "heuristic"

_TMP = Path(tempfile.mkdtemp(prefix="duet-tests-"))
os.environ["DUET_DATA_DIR"] = str(_TMP)
os.environ["DUET_DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["DUET_VAULT_PATH"] = str(_TMP / "vault.enc")
os.environ["DUET_SECRET_KEY"] = "test-key-not-for-production"

import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# tests/ is not a package, so conftest loads as a top-level module and a
# relative import of the fixtures would fail.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from duet.db import Base, SessionLocal, engine, init_db  # noqa: E402
from duet.llm.client import get_llm  # noqa: E402

# Apps the scenario covers. Registered as live for the duration of the suite so
# `connect` accepts them and the sweep has something to read.
SCENARIO_APPS = (
    "gmail", "slack", "google_calendar", "stripe", "quickbooks", "gusto",
    "hubspot", "jira", "github", "docusign", "shopify", "zendesk",
    "notion", "google_drive", "zoom", "linear", "asana", "outlook",
)


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def scenario_adapters():
    """Point the scenario apps at the scripted adapter, then put it all back.

    Saving and restoring `REAL` matters: `tests/test_live_connectors.py`
    asserts on the genuine registration table, and a leaked override would make
    those pass or fail depending on test order.
    """
    from duet.connectors import registry

    from fixtures.scenario import ScenarioConnector

    original = dict(registry.REAL)
    # Override unconditionally, including apps that now have real adapters.
    # Skipping those meant that the moment Gmail became a real integration,
    # these tests started reaching for a live OAuth token and failing on a
    # placeholder credential - a test should never depend on which adapters
    # happen to be implemented today.
    for key in SCENARIO_APPS:
        registry.register(key, lambda k=key: ScenarioConnector(k))
    registry.reset_cache()
    yield
    registry.REAL.clear()
    registry.REAL.update(original)
    registry.reset_cache()


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def user(db):
    from duet.models import User

    u = User(email="test@local", name="Test", timezone="UTC", role="owner")
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from duet.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def seeded(client):
    """A client with the scenario apps connected and one sweep done."""
    for key in SCENARIO_APPS:
        client.post(f"/api/connectors/{key}/connect", json={"grant_write": True})
    _seed_preferences(client)
    client.post("/api/sync")
    return client


def _seed_preferences(client) -> None:
    """Rules and memories, so recall and the priority engine have something."""
    from sqlalchemy import select

    from duet.agents.base import AgentContext
    from duet.agents.memory import remember
    from duet.db import session_scope
    from duet.models import PolicyRule, User

    with session_scope() as db:
        user = db.execute(select(User)).scalars().first()
        if user is None:
            return
        for kind, pattern, note in (
            ("vip", "harborventures.com", "Lead investor on the Series A"),
            ("vip", "northwind.io", "Largest customer by ARR"),
            ("mute", "standupdaily.com", "Newsletter - never surface"),
            ("boost", "invoice", "Cash collection is the current priority"),
        ):
            db.add(PolicyRule(user_id=user.id, kind=kind, pattern=pattern, note=note))

        ctx = AgentContext(db=db, user=user, llm=get_llm(), intent="")
        for content, tags in (
            ("Prefers short, direct messages with no filler openers.", ["style"]),
            ("Raising a Series A led by Harbor Ventures; Dana is the partner.", ["company"]),
            ("Northwind is the largest customer at $84k ARR.", ["customers"]),
            ("Wants to be asked before anything is sent to an investor.", ["preference"]),
        ):
            remember(ctx, content, kind="long", tags=tags, source="seed", salience=0.85)


@pytest.fixture
def ctx(db, user):
    from duet.agents.base import AgentContext

    return AgentContext(db=db, user=user, llm=get_llm())
