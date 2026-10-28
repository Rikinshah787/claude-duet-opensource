"""Agent 8 - Memory.

Four kinds, each with a different lifetime and a different job:

* **short**    - the live thread. Trimmed aggressively; it is context, not record.
* **long**     - durable facts about the person: how they work, who matters,
                 what they have told the assistant to do differently.
* **semantic** - retrievable knowledge, ranked by similarity. Backed by a hash
                 embedding locally and by Qdrant in deployment; `recall()` is
                 the only thing that has to change.
* **episodic** - what the assistant actually did, so "have we dealt with this
                 before?" has an answer.

Writing to long-term memory is deliberately conservative. An assistant that
over-remembers becomes an assistant that acts on a throwaway remark from six
weeks ago, so only explicit preference statements are promoted.
"""
from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from ..models import Memory, MemoryKind
from .base import Agent, AgentContext, AgentResult

EMBED_DIM = 96

# Phrases that mark a durable instruction rather than a passing comment.
PREFERENCE_MARKERS = (
    re.compile(r"\b(?:always|never|from now on|going forward|in future|stop|don'?t ever)\b", re.I),
    re.compile(r"\bi (?:prefer|like|want|need|hate|don'?t want)\b", re.I),
    re.compile(r"\b(?:remember|keep in mind|note) that\b", re.I),
    re.compile(r"\bmy (?:preference|rule|policy) is\b", re.I),
)


def embed(text: str) -> list[float]:
    """Deterministic hashing embedding.

    Not a semantic model - a bag-of-words projection that is stable, offline and
    good enough to rank a few thousand rows. Swap for a real encoder by changing
    this one function; nothing else depends on how the vector is produced.
    """
    vec = [0.0] * EMBED_DIM
    tokens = re.findall(r"[a-z0-9]{2,}", text.lower())
    for tok in tokens:
        h = int(hashlib.blake2b(tok.encode(), digest_size=8).hexdigest(), 16)
        vec[h % EMBED_DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


class MemoryAgent(Agent):
    name = "memory"
    role = "Memory"
    capabilities = ("recall", "preference learning", "episodic history")

    def run(self, ctx: AgentContext) -> AgentResult:
        mode = ctx.step_input.get("mode", "recall")
        if mode == "write":
            return self._write(ctx)
        return self._recall(ctx)

    # ------------------------------------------------------------------ #
    def _recall(self, ctx: AgentContext) -> AgentResult:
        query = ctx.step_input.get("query") or ctx.intent
        limit = int(ctx.step_input.get("limit", 6))
        hits = recall(ctx, query, limit=limit)
        return AgentResult(
            ok=True,
            summary=f"Recalled {len(hits)} item(s).",
            output={"memories": [m.content for m in hits], "ids": [m.id for m in hits]},
        )

    def _write(self, ctx: AgentContext) -> AgentResult:
        content = (ctx.step_input.get("content") or "").strip()
        if not content:
            return AgentResult(ok=False, summary="Nothing to remember", error="empty")
        kind = ctx.step_input.get("kind", MemoryKind.EPISODIC.value)
        mem = remember(
            ctx,
            content,
            kind=kind,
            tags=ctx.step_input.get("tags", []),
            source=ctx.step_input.get("source", "agent"),
            salience=float(ctx.step_input.get("salience", 0.5)),
        )
        return AgentResult(ok=True, summary="Remembered.", output={"id": mem.id})


# --------------------------------------------------------------------------- #
# Module-level helpers - used by the orchestrator directly.
# --------------------------------------------------------------------------- #
def remember(
    ctx: AgentContext,
    content: str,
    *,
    kind: str = MemoryKind.EPISODIC.value,
    tags: list[str] | None = None,
    source: str = "",
    salience: float = 0.5,
    key: str | None = None,
) -> Memory:
    # Collapse duplicates on an explicit key rather than accumulating near-copies.
    if key:
        existing = ctx.db.execute(
            select(Memory).where(Memory.user_id == ctx.user.id, Memory.key == key)
        ).scalar_one_or_none()
        if existing:
            existing.content = content
            existing.salience = max(existing.salience, salience)
            existing.embedding = embed(content)
            existing.updated_at = datetime.now(timezone.utc)
            ctx.db.flush()
            return existing

    mem = Memory(
        user_id=ctx.user.id,
        kind=kind,
        key=key,
        content=content[:4000],
        tags=tags or [],
        source=source,
        salience=salience,
        embedding=embed(content),
    )
    ctx.db.add(mem)
    ctx.db.flush()
    return mem


def recall(ctx: AgentContext, query: str, *, limit: int = 6) -> list[Memory]:
    """Rank by similarity, weighted by salience and how recently it was useful."""
    rows = list(
        ctx.db.execute(select(Memory).where(Memory.user_id == ctx.user.id)).scalars()
    )
    if not rows:
        return []

    qv = embed(query or "")
    now = datetime.now(timezone.utc)
    scored: list[tuple[float, Memory]] = []

    for mem in rows:
        sim = cosine(qv, mem.embedding or [])
        age_days = max(0.0, (now - mem.created_at.replace(tzinfo=timezone.utc)).days)
        recency = 1.0 / (1.0 + age_days / 45.0)
        score = sim * 0.6 + mem.salience * 0.3 + recency * 0.1
        # Long-term preferences stay reachable even on a weak lexical match.
        if mem.kind == MemoryKind.LONG.value:
            score += 0.12
        scored.append((score, mem))

    scored.sort(key=lambda p: p[0], reverse=True)
    picked = [m for s, m in scored[:limit] if s > 0.05]

    for mem in picked:
        mem.use_count += 1
        mem.last_used_at = now
    ctx.db.flush()
    return picked


def capture_preference(ctx: AgentContext, utterance: str) -> Memory | None:
    """Promote an explicit instruction to long-term memory. Conservative by design."""
    text = utterance.strip()
    if len(text) < 12 or not any(p.search(text) for p in PREFERENCE_MARKERS):
        return None

    key = "pref:" + hashlib.blake2b(
        re.sub(r"\W+", " ", text.lower()).strip().encode(), digest_size=8
    ).hexdigest()
    return remember(
        ctx,
        text,
        kind=MemoryKind.LONG.value,
        tags=["preference"],
        source="user",
        salience=0.9,
        key=key,
    )


def record_episode(ctx: AgentContext, title: str, outcome: str, tags: list[str]) -> Memory:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return remember(
        ctx,
        f"[{stamp}] {title} - {outcome}",
        kind=MemoryKind.EPISODIC.value,
        tags=tags,
        source="task",
        salience=0.4,
    )


def behaviour_history(ctx_db, user_id: str) -> dict[str, dict[str, int]]:
    """How the user has historically handled each signal kind.

    Feeds `historical_factor` in the priority engine, which is how the system
    stops surfacing a category the user keeps dismissing.
    """
    from ..models import Signal

    rows = list(
        ctx_db.execute(
            select(Signal.kind, Signal.state).where(Signal.user_id == user_id)
        ).all()
    )
    stats: dict[str, dict[str, int]] = {}
    for kind, state in rows:
        bucket = stats.setdefault(kind, {"handled": 0, "dismissed": 0})
        if state == "handled":
            bucket["handled"] += 1
        elif state == "dismissed":
            bucket["dismissed"] += 1
    return stats
