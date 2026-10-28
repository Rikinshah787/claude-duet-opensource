"""Connected apps and user settings.

The connector gallery is served from the static catalog joined against this
user's live connections, so the UI can show the full surface (what is
*connectable*) and the current state (what is *connected*) in one call.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..connectors import catalog, registry
from ..core import audit, oauth
from ..core.vault import get_vault
from ..models import Connection, PolicyRule, Signal
from .deps import ActorDep, DbDep, UserDep

router = APIRouter(tags=["apps"])


def _serialize_connection(c: Connection) -> dict[str, Any]:
    return {
        "id": c.id,
        "status": c.status,
        "enabled": c.enabled,
        "monitored": c.monitored,
        "account_label": c.account_label,
        "scopes": c.scopes or [],
        "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
        "last_error": c.last_error,
    }


@router.get("/connectors")
def list_connectors(db: DbDep, user: UserDep) -> dict[str, Any]:
    connections = {
        c.connector_key: c
        for c in db.execute(
            select(Connection).where(Connection.user_id == user.id)
        ).scalars()
    }
    counts = dict(
        db.execute(
            select(Signal.connector_key, func.count(Signal.id))
            .where(Signal.user_id == user.id)
            .group_by(Signal.connector_key)
        ).all()
    )

    items = []
    for spec in catalog.CATALOG:
        conn = connections.get(spec.key)
        items.append(
            {
                "key": spec.key,
                "name": spec.name,
                "category": spec.category,
                "color": spec.color,
                "glyph": spec.glyph,
                "summary": spec.summary,
                "auth": spec.auth,
                "tags": list(spec.tags),
                "signal_kinds": list(spec.signal_kinds),
                "read_scopes": list(spec.read_scopes),
                "write_scopes": list(spec.write_scopes),
                "writes": [
                    {
                        "key": w.key,
                        "label": w.label,
                        "risk": w.risk.value,
                        "reversible": w.reversible,
                        "note": w.note,
                    }
                    for w in spec.writes
                ],
                "monitored": spec.monitored,
                "has_adapter": spec.has_adapter,
                "live": registry.is_live(spec.key),
                # Catalogued is not the same as usable. The UI must show the
                # difference rather than offering a Connect button that leads
                # nowhere.
                "available": registry.available(spec.key),
                # Lets the UI offer "Sign in with X" instead of a token box.
                "oauth": oauth.status(spec.key),
                # A revoked row is kept for its audit trail, so presence is
                # not connectedness - the credential is. Disconnect clears the
                # ref, and without one no adapter can do anything.
                "connected": conn is not None and conn.credential_ref is not None,
                "connection": _serialize_connection(conn) if conn else None,
                "signal_count": counts.get(spec.key, 0),
            }
        )

    return {"connectors": items, "categories": list(catalog.CATEGORIES)}


class ConnectRequest(BaseModel):
    account_label: str | None = None
    grant_write: bool = True
    # Whatever the app issues: a token, IMAP host/user/app-password, or
    # nothing at all for adapters that reuse a machine-level sign-in. Apps that
    # support OAuth skip this route entirely and go through /api/oauth/*.
    credential: dict[str, Any] | None = None


@router.post("/connectors/{key}/connect")
def connect(key: str, payload: ConnectRequest, db: DbDep, user: ActorDep) -> dict[str, Any]:
    spec = catalog.get(key)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown connector: {key}")
    if not registry.available(key):
        raise HTTPException(
            status_code=409,
            detail=(
                f"{spec.name} is in the catalog but has no integration yet. "
                "Connecting it would look like it works and never produce anything."
            ),
        )

    existing = db.execute(
        select(Connection).where(
            Connection.user_id == user.id, Connection.connector_key == key
        )
    ).scalar_one_or_none()

    scopes = list(spec.read_scopes) + (list(spec.write_scopes) if payload.grant_write else [])
    handle = get_vault().put(
        user.id,
        key,
        # No credential is legitimate for adapters that reuse an existing
        # machine-level sign-in (the GitHub adapter uses `gh auth`).
        payload.credential or {"type": "delegated", "note": "uses local CLI auth"},
    )

    if existing:
        existing.status = "connected"
        existing.enabled = True
        existing.scopes = scopes
        existing.credential_ref = handle.ref
        existing.last_error = None
        conn = existing
    else:
        conn = Connection(
            user_id=user.id,
            connector_key=key,
            display_name=spec.name,
            account_label=payload.account_label or f"{spec.name} (local)",
            scopes=scopes,
            credential_ref=handle.ref,
            monitored=spec.monitored,
        )
        db.add(conn)

    db.flush()
    audit.record(
        db,
        action="connector.connected",
        actor=f"user:{user.email}",
        user_id=user.id,
        resource_type="connection",
        resource_id=conn.id,
        detail={"connector": key, "scopes": scopes, "write_granted": payload.grant_write},
    )
    db.commit()
    return {"connection": {"connector_key": key, **_serialize_connection(conn)}}


@router.post("/connectors/{key}/disconnect")
def disconnect(key: str, db: DbDep, user: ActorDep) -> dict[str, Any]:
    """Disconnecting destroys the credential, not just the row."""
    conn = db.execute(
        select(Connection).where(
            Connection.user_id == user.id, Connection.connector_key == key
        )
    ).scalar_one_or_none()
    if conn is None:
        raise HTTPException(status_code=404, detail=f"{key} is not connected")

    if conn.credential_ref:
        get_vault().forget(conn.credential_ref)

    conn.status = "revoked"
    conn.enabled = False
    conn.credential_ref = None
    conn.scopes = []

    audit.record(
        db,
        action="connector.disconnected",
        actor=f"user:{user.email}",
        user_id=user.id,
        resource_type="connection",
        resource_id=conn.id,
        detail={"connector": key, "credential_destroyed": True},
    )
    db.commit()
    return {"ok": True, "connector_key": key}


class ConnectionPatch(BaseModel):
    monitored: bool | None = None
    enabled: bool | None = None


@router.patch("/connectors/{key}")
def patch_connection(
    key: str, payload: ConnectionPatch, db: DbDep, user: ActorDep
) -> dict[str, Any]:
    conn = db.execute(
        select(Connection).where(
            Connection.user_id == user.id, Connection.connector_key == key
        )
    ).scalar_one_or_none()
    if conn is None:
        raise HTTPException(status_code=404, detail=f"{key} is not connected")

    if payload.monitored is not None:
        conn.monitored = payload.monitored
    if payload.enabled is not None:
        conn.enabled = payload.enabled
    db.commit()
    return {"connection": {"connector_key": key, **_serialize_connection(conn)}}


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def _serialize_rule(r: PolicyRule) -> dict[str, Any]:
    return {
        "id": r.id,
        "kind": r.kind,
        "pattern": r.pattern,
        "weight": r.weight,
        "note": r.note,
        "enabled": r.enabled,
    }


@router.get("/settings")
def get_settings_route(db: DbDep, user: UserDep) -> dict[str, Any]:
    from ..config import settings as cfg

    rules = list(
        db.execute(select(PolicyRule).where(PolicyRule.user_id == user.id)).scalars()
    )
    return {
        "quiet_hours": user.quiet_hours or {"start": 21, "end": 7},
        "briefing_hour": cfg.briefing_hour,
        "briefing_optin": user.briefing_optin,
        "timezone": user.timezone,
        "finance": user.finance or {},
        "sweep_interval_s": cfg.sweep_interval_s,
        "rules": [_serialize_rule(r) for r in rules],
    }


class SettingsPatch(BaseModel):
    quiet_hours: dict[str, int] | None = None
    timezone: str | None = None
    briefing_optin: bool | None = None
    # Real cash figures. Without these the forecast declines to project rather
    # than inventing a starting balance.
    finance: dict[str, float] | None = None


@router.patch("/settings")
def patch_settings(payload: SettingsPatch, db: DbDep, user: ActorDep) -> dict[str, Any]:
    if payload.quiet_hours is not None:
        start = int(payload.quiet_hours.get("start", 21)) % 24
        end = int(payload.quiet_hours.get("end", 7)) % 24
        user.quiet_hours = {"start": start, "end": end}
    if payload.timezone is not None:
        user.timezone = payload.timezone[:64]
    if payload.briefing_optin is not None:
        user.briefing_optin = payload.briefing_optin
    if payload.finance is not None:
        allowed = {"starting_cash", "monthly_burn", "ar_collection_rate"}
        user.finance = {
            k: float(v) for k, v in payload.finance.items() if k in allowed and v is not None
        }
    db.commit()
    return {
        "ok": True,
        "quiet_hours": user.quiet_hours,
        "timezone": user.timezone,
        "finance": user.finance,
    }


class NewRule(BaseModel):
    # No "auto_approve" kind: nothing consumes it, and a rule a user can set
    # that silently does nothing is worse than an absent feature when the
    # feature is about skipping approvals.
    kind: str = Field(pattern="^(vip|mute|boost|never)$")
    pattern: str = Field(min_length=1, max_length=300)
    weight: float = 1.0
    note: str = ""


@router.post("/settings/rules", status_code=201)
def add_rule(payload: NewRule, db: DbDep, user: ActorDep) -> dict[str, Any]:
    rule = PolicyRule(
        user_id=user.id,
        kind=payload.kind,
        pattern=payload.pattern,
        weight=max(0.0, min(3.0, payload.weight)),
        note=payload.note,
    )
    db.add(rule)
    db.commit()
    return {"rule": _serialize_rule(rule)}


@router.delete("/settings/rules/{rule_id}")
def delete_rule(rule_id: str, db: DbDep, user: ActorDep) -> dict[str, Any]:
    rule = db.get(PolicyRule, rule_id)
    if rule is None or rule.user_id != user.id:
        raise HTTPException(status_code=404, detail="No such rule")
    db.delete(rule)
    db.commit()
    return {"ok": True}
