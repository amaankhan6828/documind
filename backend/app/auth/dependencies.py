"""FastAPI dependency for the authenticated user context."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.app.auth.tokens import InvalidAccessToken, decode_access_token
from backend.app.db.session import get_db
from backend.app.models import User

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized

    try:
        identity = decode_access_token(credentials.credentials)
    except InvalidAccessToken:
        raise unauthorized from None

    user = db.get(User, identity.user_id)
    if user is None or user.tenant_id != identity.tenant_id:
        raise unauthorized
    return user
