"""OAuth sign-in endpoints.

The callback is a browser redirect, not an API call, so it returns a small HTML
page rather than JSON: the user lands here from the provider and needs to be
told what happened in a window they can close.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from ..connectors import catalog
from ..core import audit, oauth
from ..models import Connection
from .deps import ActorDep, DbDep, UserDep

router = APIRouter(tags=["oauth"])


@router.get("/oauth/{connector_key}/status")
def oauth_status(connector_key: str, user: UserDep) -> dict[str, Any]:
    """Whether this app can use OAuth, and whether it is set up on this machine."""
    return oauth.status(connector_key)


@router.post("/oauth/{connector_key}/start")
def oauth_start(connector_key: str, user: ActorDep) -> dict[str, Any]:
    """Return the provider URL to open. The browser does the rest."""
    try:
        return oauth.start(connector_key, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        # Not configured on this machine - actionable, so 409 with the steps.
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/oauth/callback", response_class=HTMLResponse)
def oauth_callback(
    db: DbDep,
    user: UserDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    if error:
        return _page("Sign-in cancelled", f"{error}. Nothing was connected.", ok=False)
    if not code or not state:
        return _page("Something went wrong", "The provider did not return a code.", ok=False)

    try:
        result = oauth.finish(state, code)
    except PermissionError as exc:
        return _page("That link expired", str(exc), ok=False)
    except Exception as exc:
        return _page("Could not complete sign-in", f"{type(exc).__name__}: {exc}", ok=False)

    connector_key = result["connector_key"]
    spec = catalog.get(connector_key)

    conn = db.execute(
        select(Connection).where(
            Connection.user_id == user.id, Connection.connector_key == connector_key
        )
    ).scalar_one_or_none()

    # The vault key is derived from (user, connector), so the handle ref is
    # stable and recomputing it here matches what oauth stored.
    from ..core.vault import get_vault
    import hashlib

    ref = hashlib.sha256(f"{user.id}:{connector_key}".encode()).hexdigest()[:32]

    if conn is None:
        conn = Connection(
            user_id=user.id,
            connector_key=connector_key,
            display_name=spec.name if spec else connector_key,
            account_label=f"{result['provider']} (OAuth)",
        )
        db.add(conn)
    conn.status = "connected"
    conn.enabled = True
    conn.credential_ref = ref
    conn.scopes = result["scopes"]
    conn.last_error = None
    db.flush()

    audit.record(
        db,
        action="connector.connected",
        actor=f"user:{user.email}",
        user_id=user.id,
        resource_type="connection",
        resource_id=conn.id,
        detail={
            "connector": connector_key,
            "method": "oauth2",
            "provider": result["provider"],
            "scopes": result["scopes"],
            "refreshable": result["has_refresh"],
        },
    )
    db.commit()

    warning = ""
    if not result["has_refresh"]:
        # Without one the grant dies in an hour and the user has no idea why.
        warning = (
            "<p class='warn'>The provider did not return a refresh token, so this "
            "connection will expire in about an hour. Disconnect and reconnect, "
            "and approve the consent screen when it appears.</p>"
        )

    name = spec.name if spec else connector_key
    return _page(
        f"{name} is connected",
        f"{len(result['scopes'])} scope(s) granted. You can close this tab and "
        "go back to the app.",
        ok=True,
        extra=warning,
    )


def _page(title: str, body: str, *, ok: bool, extra: str = "") -> HTMLResponse:
    accent = "#6B8F71" if ok else "#C2553F"
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: dark light; }}
  body {{ margin:0; min-height:100vh; display:grid; place-items:center;
         background:#0F0F0E; color:#F5F4F1;
         font:16px/1.6 system-ui,-apple-system,Segoe UI,sans-serif; padding:24px; }}
  .card {{ max-width:30rem; background:#1A1917; border:1px solid rgba(255,255,255,.08);
          border-radius:14px; padding:28px 30px; }}
  h1 {{ font:600 22px/1.3 ui-serif,Georgia,serif; margin:0 0 10px; color:{accent}; }}
  p {{ margin:0 0 10px; color:#A3A096; }}
  .warn {{ color:#C89B3C; }}
  a {{ color:#D97757; }}
  @media (prefers-color-scheme: light) {{
    body {{ background:#FAF9F6; color:#1F1E1B; }}
    .card {{ background:#FFF; border-color:rgba(0,0,0,.1); }}
    p {{ color:#5C5950; }}
  }}
</style></head>
<body><div class="card">
  <h1>{title}</h1><p>{body}</p>{extra}
  <p><a href="http://localhost:3000">Back to the app</a></p>
</div></body></html>"""
    return HTMLResponse(html, status_code=200 if ok else 400)
