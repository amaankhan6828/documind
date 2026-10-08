from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.auth.passwords import verify_password
from backend.app.core.config import get_settings
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.models import Tenant, User

TEST_SECRET = "test-only-auth-secret-with-at-least-32-bytes"


@pytest.fixture
def auth_client(
    db_engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    get_settings.cache_clear()

    def override_get_db() -> Iterator[Session]:
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def create_tenant(db_session: Session, slug: str = "auth-test") -> UUID:
    tenant = Tenant(name="Authentication Test", slug=slug)
    db_session.add(tenant)
    db_session.commit()
    return tenant.id


def registration_payload(tenant_id: UUID, **overrides: object) -> dict[str, object]:
    return {
        "email": "user@example.com",
        "password": "a-strong-test-password",
        "display_name": "Example User",
        "tenant_id": str(tenant_id),
        **overrides,
    }


def login_payload(tenant_id: UUID, **overrides: object) -> dict[str, object]:
    return {
        "email": "user@example.com",
        "password": "a-strong-test-password",
        "tenant_id": str(tenant_id),
        **overrides,
    }


def register_user(
    client: TestClient, tenant_id: UUID, **overrides: object
) -> dict[str, object]:
    response = client.post(
        "/api/v1/auth/register", json=registration_payload(tenant_id, **overrides)
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_register_stores_hash_and_returns_safe_user(
    auth_client: TestClient, db_session: Session
) -> None:
    tenant_id = create_tenant(db_session)

    response = auth_client.post(
        "/api/v1/auth/register", json=registration_payload(tenant_id)
    )

    assert response.status_code == 201
    result = response.json()
    assert set(result) == {"id", "email", "display_name", "tenant_id"}
    assert result["email"] == "user@example.com"
    assert result["tenant_id"] == str(tenant_id)
    user = db_session.scalar(select(User).where(User.id == UUID(result["id"])))
    assert user is not None
    assert user.tenant_id == tenant_id
    assert user.password_hash != "a-strong-test-password"
    assert user.password_hash is not None
    assert user.password_hash.startswith("$argon2id$")
    assert verify_password("a-strong-test-password", user.password_hash)


def test_register_normalizes_email_and_rejects_duplicate(
    auth_client: TestClient, db_session: Session
) -> None:
    tenant_id = create_tenant(db_session)
    register_user(auth_client, tenant_id, email=" User@Example.com ")

    response = auth_client.post(
        "/api/v1/auth/register", json=registration_payload(tenant_id)
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "An account with these details already exists"


def test_register_requires_existing_tenant(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/auth/register", json=registration_payload(uuid4())
    )

    assert response.status_code == 400


def test_registration_and_login_are_scoped_to_tenant(
    auth_client: TestClient, db_session: Session
) -> None:
    first_tenant = create_tenant(db_session, "first-tenant")
    second_tenant = create_tenant(db_session, "second-tenant")
    register_user(auth_client, first_tenant)
    register_user(
        auth_client,
        second_tenant,
        password="another-strong-password",
    )

    first_login = auth_client.post(
        "/api/v1/auth/login", json=login_payload(first_tenant)
    )
    second_login = auth_client.post(
        "/api/v1/auth/login",
        json=login_payload(
            second_tenant, password="another-strong-password"
        ),
    )

    assert first_login.status_code == second_login.status_code == 200
    first_claims = jwt.decode(
        first_login.json()["access_token"], TEST_SECRET, algorithms=["HS256"]
    )
    second_claims = jwt.decode(
        second_login.json()["access_token"], TEST_SECRET, algorithms=["HS256"]
    )
    assert first_claims["tenant_id"] == str(first_tenant)
    assert second_claims["tenant_id"] == str(second_tenant)


def test_register_rejects_short_password(auth_client: TestClient, db_session: Session) -> None:
    tenant_id = create_tenant(db_session)
    response = auth_client.post(
        "/api/v1/auth/register",
        json=registration_payload(tenant_id, password="too-short"),
    )

    assert response.status_code == 422


def test_login_returns_signed_bearer_token(auth_client: TestClient, db_session: Session) -> None:
    tenant_id = create_tenant(db_session)
    register_user(auth_client, tenant_id)

    response = auth_client.post(
        "/api/v1/auth/login", json=login_payload(tenant_id)
    )

    assert response.status_code == 200
    result = response.json()
    assert set(result) == {"access_token", "token_type"}
    assert result["token_type"] == "bearer"
    claims = jwt.decode(result["access_token"], TEST_SECRET, algorithms=["HS256"])
    user = db_session.scalar(select(User).where(User.email == "user@example.com"))
    assert user is not None
    assert claims["sub"] == str(user.id)
    assert claims["tenant_id"] == str(tenant_id)
    assert "exp" in claims and "iat" in claims


def test_login_rejects_wrong_password_and_unknown_account(
    auth_client: TestClient, db_session: Session
) -> None:
    tenant_id = create_tenant(db_session)
    register_user(auth_client, tenant_id)

    wrong_password = auth_client.post(
        "/api/v1/auth/login",
        json=login_payload(tenant_id, password="incorrect-password"),
    )
    unknown_account = auth_client.post(
        "/api/v1/auth/login",
        json=login_payload(tenant_id, email="unknown@example.com"),
    )

    assert wrong_password.status_code == unknown_account.status_code == 401
    assert wrong_password.json() == unknown_account.json()


@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer not-a-jwt", "Bearer a.b.c"],
)
def test_me_rejects_missing_or_malformed_token(
    auth_client: TestClient, authorization: str | None
) -> None:
    headers = {"Authorization": authorization} if authorization else None
    response = auth_client.get("/api/v1/auth/me", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_rejects_bad_signature_expiration_and_tenant_claim(
    auth_client: TestClient, db_session: Session
) -> None:
    tenant_id = create_tenant(db_session)
    user = register_user(auth_client, tenant_id)
    user_id = user["id"]
    now = datetime.now(UTC)
    invalid_tokens = [
        jwt.encode(
            {
                "sub": user_id,
                "tenant_id": str(tenant_id),
                "iat": now,
                "exp": now + timedelta(minutes=5),
            },
            "different-test-secret-with-more-than-32-bytes",
            algorithm="HS256",
        ),
        jwt.encode(
            {
                "sub": user_id,
                "tenant_id": str(tenant_id),
                "iat": now - timedelta(minutes=2),
                "exp": now - timedelta(minutes=1),
            },
            TEST_SECRET,
            algorithm="HS256",
        ),
        jwt.encode(
            {
                "sub": user_id,
                "tenant_id": str(uuid4()),
                "iat": now,
                "exp": now + timedelta(minutes=5),
            },
            TEST_SECRET,
            algorithm="HS256",
        ),
    ]

    for token in invalid_tokens:
        response = auth_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401


def test_me_returns_safe_profile_after_login(
    auth_client: TestClient, db_session: Session
) -> None:
    tenant_id = create_tenant(db_session)
    user = register_user(auth_client, tenant_id)
    login = auth_client.post("/api/v1/auth/login", json=login_payload(tenant_id))
    token = login.json()["access_token"]

    response = auth_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": user["id"],
        "email": "user@example.com",
        "display_name": "Example User",
        "tenant_id": str(tenant_id),
    }
    assert "password_hash" not in response.json()
