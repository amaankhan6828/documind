from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.auth.tokens import create_access_token
from backend.app.core.config import get_settings
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.models import Document, DocumentVersion, Tenant, User

TEST_SECRET = "test-only-document-secret-with-at-least-32-bytes"


@pytest.fixture
def document_client(db_engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
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


@pytest.fixture
def tenant_users(db_session: Session) -> dict[str, tuple[UUID, UUID]]:
    result = {}
    for suffix in ("a", "b"):
        tenant = Tenant(name=f"Tenant {suffix.upper()}", slug=f"tenant-{suffix}")
        user = User(
            tenant=tenant,
            email=f"user-{suffix}@example.com",
            display_name=f"User {suffix.upper()}",
        )
        db_session.add_all([tenant, user])
        db_session.flush()
        result[suffix] = (tenant.id, user.id)
    db_session.commit()
    return result


def auth_headers(user_id: UUID, tenant_id: UUID) -> dict[str, str]:
    token = create_access_token(user_id, tenant_id)
    return {"Authorization": f"Bearer {token}"}


def create_document(
    db_session: Session, tenant_id: UUID, title: str
) -> Document:
    document = Document(
        tenant_id=tenant_id,
        title=title,
        document_type="annual_report",
    )
    db_session.add(document)
    db_session.commit()
    return document


def test_create_assigns_authenticated_tenant_and_rejects_client_tenant(
    document_client: TestClient,
    tenant_users: dict[str, tuple[UUID, UUID]],
    db_session: Session,
) -> None:
    tenant_a, user_a = tenant_users["a"]
    tenant_b, _ = tenant_users["b"]
    headers = auth_headers(user_a, tenant_a)
    response = document_client.post(
        "/api/v1/documents",
        headers=headers,
        json={
            "title": "Annual Report 2025",
            "document_type": "annual_report",
            "issuer": "Example Corp",
            "description": "FY2025 annual report",
        },
    )

    assert response.status_code == 201, response.text
    data = response.json()
    saved = db_session.get(Document, UUID(data["id"]))
    assert saved is not None and saved.tenant_id == tenant_a
    assert data["title"] == "Annual Report 2025"
    assert "tenant_id" not in data

    spoofed = document_client.post(
        "/api/v1/documents",
        headers=headers,
        json={
            "title": "Spoof attempt",
            "document_type": "report",
            "tenant_id": str(tenant_b),
        },
    )
    assert spoofed.status_code == 422


def test_list_is_tenant_scoped_and_paginated(
    document_client: TestClient,
    tenant_users: dict[str, tuple[UUID, UUID]],
    db_session: Session,
) -> None:
    tenant_a, user_a = tenant_users["a"]
    tenant_b, _ = tenant_users["b"]
    own = create_document(db_session, tenant_a, "Tenant A document")
    create_document(db_session, tenant_b, "Tenant B document")

    response = document_client.get(
        "/api/v1/documents?limit=1&offset=0",
        headers=auth_headers(user_a, tenant_a),
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [str(own.id)]


def test_get_hides_cross_tenant_documents(
    document_client: TestClient,
    tenant_users: dict[str, tuple[UUID, UUID]],
    db_session: Session,
) -> None:
    tenant_a, user_a = tenant_users["a"]
    tenant_b, user_b = tenant_users["b"]
    doc_a = create_document(db_session, tenant_a, "A")
    doc_b = create_document(db_session, tenant_b, "B")

    for user, tenant, own, other in (
        (user_a, tenant_a, doc_a, doc_b),
        (user_b, tenant_b, doc_b, doc_a),
    ):
        headers = auth_headers(user, tenant)
        assert document_client.get(
            f"/api/v1/documents/{own.id}", headers=headers
        ).status_code == 200
        hidden = document_client.get(
            f"/api/v1/documents/{other.id}", headers=headers
        )
        missing = document_client.get(
            f"/api/v1/documents/{UUID(int=0)}", headers=headers
        )
        assert hidden.status_code == missing.status_code == 404
        assert hidden.json() == missing.json()


def test_update_allows_metadata_and_rejects_tenant_changes(
    document_client: TestClient,
    tenant_users: dict[str, tuple[UUID, UUID]],
    db_session: Session,
) -> None:
    tenant_a, user_a = tenant_users["a"]
    tenant_b, _ = tenant_users["b"]
    own = create_document(db_session, tenant_a, "Original")
    other = create_document(db_session, tenant_b, "Private")
    headers = auth_headers(user_a, tenant_a)

    updated = document_client.patch(
        f"/api/v1/documents/{own.id}",
        headers=headers,
        json={"title": "Updated", "issuer": "Example Corp"},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Updated"
    assert updated.json()["issuer"] == "Example Corp"

    tenant_change = document_client.patch(
        f"/api/v1/documents/{own.id}",
        headers=headers,
        json={"tenant_id": str(tenant_b)},
    )
    assert tenant_change.status_code == 422
    assert db_session.get(Document, own.id).tenant_id == tenant_a
    assert document_client.patch(
        f"/api/v1/documents/{other.id}", headers=headers, json={"title": "Changed"}
    ).status_code == 404
    assert document_client.patch(
        f"/api/v1/documents/{own.id}", headers=headers, json={}
    ).status_code == 422


def test_delete_is_tenant_scoped_and_cascades_versions(
    document_client: TestClient,
    tenant_users: dict[str, tuple[UUID, UUID]],
    db_session: Session,
) -> None:
    tenant_a, user_a = tenant_users["a"]
    tenant_b, _ = tenant_users["b"]
    own = create_document(db_session, tenant_a, "A")
    other = create_document(db_session, tenant_b, "B")
    own_version = DocumentVersion(
        tenant_id=tenant_a, document_id=own.id, version_number=1
    )
    other_version = DocumentVersion(
        tenant_id=tenant_b, document_id=other.id, version_number=1
    )
    db_session.add_all([own_version, other_version])
    db_session.commit()
    own_id, other_id = own.id, other.id
    own_version_id, other_version_id = own_version.id, other_version.id

    headers = auth_headers(user_a, tenant_a)
    assert document_client.delete(
        f"/api/v1/documents/{other_id}", headers=headers
    ).status_code == 404
    assert document_client.delete(
        f"/api/v1/documents/{own_id}", headers=headers
    ).status_code == 204
    db_session.expire_all()
    assert db_session.get(Document, own_id) is None
    assert db_session.get(DocumentVersion, own_version_id) is None
    assert db_session.get(Document, other_id) is not None
    assert db_session.get(DocumentVersion, other_version_id) is not None


@pytest.mark.parametrize(
    "method,path,json_body",
    [
        ("post", "/api/v1/documents", {"title": "A", "document_type": "x"}),
        ("get", "/api/v1/documents", None),
        ("get", "/api/v1/documents/00000000-0000-0000-0000-000000000000", None),
        (
            "patch",
            "/api/v1/documents/00000000-0000-0000-0000-000000000000",
            {"title": "A"},
        ),
        (
            "delete",
            "/api/v1/documents/00000000-0000-0000-0000-000000000000",
            None,
        ),
    ],
)
def test_all_document_routes_require_authentication(
    document_client: TestClient,
    method: str,
    path: str,
    json_body: dict[str, str] | None,
) -> None:
    response = document_client.request(method.upper(), path, json=json_body)
    assert response.status_code == 401


def test_document_validation_and_health_regression(
    document_client: TestClient,
    tenant_users: dict[str, tuple[UUID, UUID]],
) -> None:
    tenant_id, user_id = tenant_users["a"]
    headers = auth_headers(user_id, tenant_id)
    invalid = document_client.post(
        "/api/v1/documents",
        headers=headers,
        json={"title": "   ", "document_type": "report"},
    )
    assert invalid.status_code == 422
    assert document_client.get("/health").json() == {"status": "ok"}
    me = document_client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert "password_hash" not in me.json()
