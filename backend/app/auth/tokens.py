"""JWT access-token creation and validation."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

from backend.app.core.config import Settings, get_settings


class InvalidAccessToken(Exception):
    """Raised when an access token is malformed or has invalid claims."""


@dataclass(frozen=True)
class TokenIdentity:
    user_id: UUID
    tenant_id: UUID


def create_access_token(user_id: UUID, tenant_id: UUID) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    claims = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "iat": now,
        "exp": expires_at,
    }
    return jwt.encode(
        claims,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> TokenIdentity:
    settings: Settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "tenant_id", "iat", "exp"]},
        )
        user_id = UUID(claims["sub"])
        tenant_id = UUID(claims["tenant_id"])
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise InvalidAccessToken from exc

    return TokenIdentity(user_id=user_id, tenant_id=tenant_id)
