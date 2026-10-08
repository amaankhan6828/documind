"""Registration, login, and current-user authentication endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pwdlib.exceptions import UnknownHashError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.auth.dependencies import get_current_user
from backend.app.auth.passwords import hash_password, verify_password
from backend.app.auth.tokens import create_access_token
from backend.app.db.session import get_db
from backend.app.models import Tenant, User
from backend.app.schemas.auth import (
    AccessTokenResponse,
    AuthenticatedUserResponse,
    LoginRequest,
    RegisterRequest,
)

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
DatabaseSession = Annotated[Session, Depends(get_db)]


@router.post(
    "/register",
    response_model=AuthenticatedUserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(payload: RegisterRequest, db: DatabaseSession) -> User:
    """Create a user in an existing tenant and store only a password hash."""
    tenant = db.get(Tenant, payload.tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to register with the provided tenant",
        )

    existing_user = db.scalar(
        select(User).where(
            User.tenant_id == payload.tenant_id,
            User.email == str(payload.email),
        )
    )
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with these details already exists",
        )

    user = User(
        tenant_id=payload.tenant_id,
        email=str(payload.email),
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with these details already exists",
        ) from None
    db.refresh(user)
    return user


@router.post("/login", response_model=AccessTokenResponse)
def login(payload: LoginRequest, db: DatabaseSession) -> AccessTokenResponse:
    """Verify credentials and return a short-lived bearer access token."""
    user = db.scalar(
        select(User).where(
            User.tenant_id == payload.tenant_id,
            User.email == str(payload.email),
        )
    )
    stored_hash = user.password_hash if user is not None else None

    if stored_hash is None:
        # Perform comparable password-hash work for unknown/legacy users so the
        # response does not cheaply reveal whether the account exists.
        hash_password(payload.password)
        valid_password = False
    else:
        try:
            valid_password = verify_password(payload.password, stored_hash)
        except UnknownHashError:
            hash_password(payload.password)
            valid_password = False

    if user is None or not valid_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return AccessTokenResponse(
        access_token=create_access_token(user.id, user.tenant_id)
    )


@router.get("/me", response_model=AuthenticatedUserResponse)
def read_current_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Return safe profile fields for the bearer-token identity."""
    return current_user
