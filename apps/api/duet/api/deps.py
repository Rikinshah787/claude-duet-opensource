"""Shared request dependencies.

Local-first means single-tenant by default: there is one owner and no login
screen. The `current_user` dependency is nonetheless the single place identity
is resolved, so adding real auth later is a change here and nowhere else - every
route already asks for the user rather than assuming one.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User

DEFAULT_EMAIL = "owner@local"


def get_or_create_owner(db: Session) -> User:
    user = db.execute(select(User).order_by(User.created_at.asc())).scalars().first()
    if user:
        return user
    user = User(
        email=DEFAULT_EMAIL,
        name="Owner",
        timezone="UTC",
        role="owner",
    )
    db.add(user)
    db.commit()
    return user


def current_user(
    db: Annotated[Session, Depends(get_db)],
    x_duet_user: Annotated[str | None, Header()] = None,
) -> User:
    """Resolve the acting user.

    `X-Duet-User` selects a specific account when more than one exists; without
    it the first (owner) account is used. In a multi-tenant deployment this
    becomes a verified token claim, and the RBAC check below starts mattering.
    """
    if x_duet_user:
        user = db.execute(select(User).where(User.email == x_duet_user)).scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail=f"No such user: {x_duet_user}")
        return user
    return get_or_create_owner(db)


def require_role(*roles: str):
    """RBAC guard. Viewers can read; only owners and members can act."""

    def _check(user: Annotated[User, Depends(current_user)]) -> User:
        if roles and user.role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Your role ({user.role}) can't perform this action.",
            )
        return user

    return _check


DbDep = Annotated[Session, Depends(get_db)]
UserDep = Annotated[User, Depends(current_user)]
ActorDep = Annotated[User, Depends(require_role("owner", "member"))]
