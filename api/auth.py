"""Local JWT authentication and role-based authorization.

The route layer depends only on ``verify_token`` and ``require_role``.  An
enterprise deployment can replace those seams with an OIDC verifier without
changing route declarations or business logic.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Annotated, Callable, Literal
from uuid import uuid4

import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select

from core.config import settings
from core.db import create_schema, session_scope
from core.models import User

Role = Literal["operator", "technician", "planner", "admin"]
COOKIE_NAME = "sentinel_session"
ALGORITHM = "HS256"
TOKEN_TTL = timedelta(hours=8)

# Argon2id parameters required by the security contract: 64 MiB and three
# iterations.  The parallelism setting is explicit so hashes are reproducible
# in shape across developer machines.
PASSWORD_HASHER = PasswordHasher(memory_cost=65_536, time_cost=3, parallelism=4, type=Type.ID)
SEEDED_USERS: tuple[tuple[str, Role], ...] = (
    ("operator", "operator"),
    ("technician", "technician"),
    ("planner", "planner"),
    ("admin", "admin"),
)
SEED_PASSWORD = "fixture"
_EPHEMERAL_SIGNING_KEY = secrets.token_urlsafe(32)
_SEEDED_DATABASES: set[str] = set()
_SEED_LOCK = Lock()


@dataclass(frozen=True, slots=True)
class Principal:
    username: str
    role: Role
    token_id: str
    can_switch_persona: bool = False


def _signing_key() -> str:
    """Return the configured key, or a process-local key for offline demos."""

    return settings.jwt_secret or _EPHEMERAL_SIGNING_KEY


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def seed_users(database_url: str | None = None) -> None:
    """Create the four local demo users without overwriting existing records."""

    database_key = database_url or settings.db_url
    with _SEED_LOCK:
        if database_key in _SEEDED_DATABASES:
            return
        create_schema(database_url)
        with session_scope(database_url) as session:
            existing = set(session.scalars(select(User.username)))
            created_at = int(time.time() * 1000)
            for username, role in SEEDED_USERS:
                if username not in existing:
                    session.add(
                        User(
                            username=username,
                            argon2_hash=hash_password(SEED_PASSWORD),
                            role=role,
                            created_at=created_at,
                        )
                    )
        _SEEDED_DATABASES.add(database_key)


def _unauthorized(message: str = "Authentication required") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "unauthorized", "message": message})


def _forbidden(message: str = "Insufficient role for this operation") -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "forbidden", "message": message})


def create_access_token(
    username: str,
    role: Role,
    *,
    now: datetime | None = None,
    ttl: timedelta = TOKEN_TTL,
    persona_switcher: bool = False,
) -> str:
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + ttl
    claims = {
        "sub": username,
        "role": role,
        "jti": uuid4().hex,
        "iat": issued_at,
        "exp": expires_at,
    }
    if persona_switcher:
        # This capability is issued only after an authenticated admin invokes
        # the persona-switch endpoint. It permits switching back to admin,
        # while normal authorization continues to use the active ``role``.
        claims["persona_switcher"] = True
    return jwt.encode(claims, _signing_key(), algorithm=ALGORITHM)


def verify_token(token: str | None) -> Principal:
    """Decode and validate a session token.

    This is the replacement seam for an enterprise OIDC/JWT verifier.
    """

    if not token:
        raise _unauthorized()
    try:
        claims = jwt.decode(token, _signing_key(), algorithms=[ALGORITHM], options={"require": ["sub", "role", "jti", "exp"]})
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        raise _unauthorized("Invalid or expired session") from None
    username = claims.get("sub")
    role = claims.get("role")
    token_id = claims.get("jti")
    if not isinstance(username, str) or not isinstance(role, str) or role not in {"operator", "technician", "planner", "admin"} or not isinstance(token_id, str):
        raise _unauthorized("Invalid session claims")
    can_switch_persona = role == "admin" or claims.get("persona_switcher") is True
    return Principal(username=username, role=role, token_id=token_id, can_switch_persona=can_switch_persona)  # type: ignore[arg-type]


def current_user(session_token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None) -> Principal:
    seed_users()
    return verify_token(session_token)


AuthenticatedUser = Annotated[Principal, Depends(current_user)]


def require_authenticated(user: AuthenticatedUser) -> Principal:
    return user


def require_role(*allowed_roles: Role) -> Callable[[AuthenticatedUser], Principal]:
    """Build a FastAPI dependency enforcing one of ``allowed_roles``."""

    allowed = set(allowed_roles)

    def dependency(user: AuthenticatedUser) -> Principal:
        if user.role not in allowed:
            raise _forbidden()
        return user

    dependency.__name__ = f"require_role_{'_'.join(allowed_roles)}"
    return dependency


def authenticate_user(username: str, password: str, database_url: str | None = None) -> Principal | None:
    seed_users(database_url)
    with session_scope(database_url) as session:
        user = session.get(User, username)
        if user is None or not verify_password(password, user.argon2_hash):
            return None
        role = user.role
    if role not in {"operator", "technician", "planner", "admin"}:
        return None
    return Principal(username=username, role=role, token_id="")  # type: ignore[arg-type]
