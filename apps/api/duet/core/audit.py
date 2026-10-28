"""Append-only, hash-chained audit log.

Every row commits to the one before it, so removing or editing an entry after
the fact breaks verification for the whole tail. This is what makes "what did it
do while I was away?" answerable with evidence rather than assertion.

What gets logged: every sweep, every score, every plan, every approval decision,
every executed write, every revert, every connector disconnect.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditLog
from .redaction import redact


def _digest(prev_hash: str, row: dict[str, Any]) -> str:
    payload = json.dumps(row, sort_keys=True, default=str)
    return hashlib.sha256(f"{prev_hash}|{payload}".encode("utf-8")).hexdigest()


def record(
    db: Session,
    *,
    action: str,
    actor: str = "system",
    user_id: str | None = None,
    resource_type: str = "",
    resource_id: str | None = None,
    outcome: str = "ok",
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    prev = db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1)).scalar_one_or_none()
    prev_hash = prev.hash if prev else ""

    safe_detail = redact(detail or {})
    row = {
        "action": action,
        "actor": actor,
        "user_id": user_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "outcome": outcome,
        "detail": safe_detail,
    }
    entry = AuditLog(
        action=action,
        actor=actor,
        user_id=user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        detail=safe_detail,
        prev_hash=prev_hash,
        hash=_digest(prev_hash, row),
    )
    db.add(entry)
    db.flush()
    return entry


def verify_chain(db: Session, limit: int | None = None) -> dict[str, Any]:
    """Recompute every digest. Returns the first break, if any."""
    stmt = select(AuditLog).order_by(AuditLog.id.asc())
    if limit:
        stmt = stmt.limit(limit)
    rows = list(db.execute(stmt).scalars())

    prev_hash = ""
    for row in rows:
        expected = _digest(
            prev_hash,
            {
                "action": row.action,
                "actor": row.actor,
                "user_id": row.user_id,
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "outcome": row.outcome,
                "detail": row.detail,
            },
        )
        if row.prev_hash != prev_hash or row.hash != expected:
            return {
                "valid": False,
                "checked": len(rows),
                "broken_at": row.id,
                "at": row.at.isoformat() if isinstance(row.at, datetime) else str(row.at),
            }
        prev_hash = row.hash

    return {"valid": True, "checked": len(rows), "head": prev_hash[:16]}
