"""Authenticated API tests for tenant-secured hybrid search."""

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.auth.tokens import create_access_token
from backend.app.api.v1 import search as search_api
from backend.app.core.config import get_settings
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.models import Tenant, User
from backend.app.retrieval.opensearch import OpenSearchIndexError
from backend.app.retrieval.qdrant import QdrantIndexError
from backend.app.retrieval.schemas import HybridCandidate

TEST_SECRET = "test-only-hybrid-search-secret-at-least-32-bytes"


@pytest.fixture
def search_client(db_engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
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
        tenant = Tenant(name=f"Search tenant {suffix}", slug=f"search-tenant-{suffix}")
        user = User(
            tenant=tenant,
            email=f"search-{suffix}@example.com",
            display_name=f"Search User {suffix}",
        )
        db_session.add_all([tenant, user])
        db_session.flush()
        result[suffix] = tenant.id, user.id
    db_session.commit()
    return result


def auth_headers(user_id: UUID, tenant_id: UUID) -> dict[str, str]:
    token = create_access_token(user_id, tenant_id)
    return {"Authorization": f"Bearer {token}"}


def test_hybrid_search_requires_authentication(search_client: TestClient) -> None:
    response = search_client.post("/api/v1/search/hybrid", json={"query": "revenue"})
    assert response.status_code == 401


def test_hybrid_search_uses_authenticated_tenant_and_returns_typed_results(
    search_client: TestClient,
    tenant_users: dict[str, tuple[UUID, UUID]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, user_id = tenant_users["a"]
    chunk_id = uuid4()
    element_id = uuid4()
    page_id = uuid4()
    calls: list[tuple[UUID, str, dict]] = []

    def fake_search(
        actual_tenant_id: UUID,
        query: str,
        *,
        dense_candidate_limit: int,
        lexical_candidate_limit: int,
        top_k: int,
        rrf_k: int,
    ) -> list[HybridCandidate]:
        calls.append(
            (
                actual_tenant_id,
                query,
                {
                    "dense_candidate_limit": dense_candidate_limit,
                    "lexical_candidate_limit": lexical_candidate_limit,
                    "top_k": top_k,
                    "rrf_k": rrf_k,
                },
            )
        )
        return [
            HybridCandidate(
                chunk_id=chunk_id,
                score=0.03,
                payload={
                    "tenant_id": str(tenant_id),
                    "sources": [
                        {
                            "document_element_id": str(element_id),
                            "source_ref": "#/texts/2",
                            "element_type": "TEXT",
                            "page_id": str(page_id),
                            "page_number": 3,
                        }
                    ],
                },
                dense_rank=2,
                dense_score=0.81,
                lexical_rank=1,
                lexical_score=4.2,
            )
        ]

    monkeypatch.setattr(search_api, "search_hybrid", fake_search)
    response = search_client.post(
        "/api/v1/search/hybrid",
        headers=auth_headers(user_id, tenant_id),
        json={
            "query": "  quarterly revenue  ",
            "dense_candidate_limit": 7,
            "lexical_candidate_limit": 9,
            "top_k": 4,
            "rrf_k": 55,
        },
    )

    assert response.status_code == 200, response.text
    assert calls == [
        (
            tenant_id,
            "quarterly revenue",
            {
                "dense_candidate_limit": 7,
                "lexical_candidate_limit": 9,
                "top_k": 4,
                "rrf_k": 55,
            },
        )
    ]
    body = response.json()
    assert set(body) == {"results"}
    assert body["results"] == [
        {
            "chunk_id": str(chunk_id),
            "score": 0.03,
            "payload": {
                "tenant_id": str(tenant_id),
                "sources": [
                    {
                        "document_element_id": str(element_id),
                        "source_ref": "#/texts/2",
                        "element_type": "TEXT",
                        "page_id": str(page_id),
                        "page_number": 3,
                    }
                ],
            },
            "dense_rank": 2,
            "dense_score": 0.81,
            "lexical_rank": 1,
            "lexical_score": 4.2,
        }
    ]


def test_forged_tenant_id_is_rejected_and_never_passed_to_retrieval(
    search_client: TestClient,
    tenant_users: dict[str, tuple[UUID, UUID]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_a, user_a = tenant_users["a"]
    tenant_b, _user_b = tenant_users["b"]
    calls = []
    monkeypatch.setattr(
        search_api,
        "search_hybrid",
        lambda *args, **kwargs: calls.append((args, kwargs)) or [],
    )

    response = search_client.post(
        "/api/v1/search/hybrid",
        headers=auth_headers(user_a, tenant_a),
        json={"query": "revenue", "tenant_id": str(tenant_b)},
    )

    assert response.status_code == 422
    assert calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {"query": " "},
        {"query": ""},
        {"query": "revenue", "dense_candidate_limit": 0},
        {"query": "revenue", "dense_candidate_limit": 101},
        {"query": "revenue", "lexical_candidate_limit": 0},
        {"query": "revenue", "lexical_candidate_limit": 101},
        {"query": "revenue", "top_k": 0},
        {"query": "revenue", "top_k": 101},
        {"query": "revenue", "rrf_k": 0},
    ],
)
def test_hybrid_search_validates_query_and_numeric_parameters(
    search_client: TestClient,
    tenant_users: dict[str, tuple[UUID, UUID]],
    payload: dict,
) -> None:
    tenant_id, user_id = tenant_users["a"]

    response = search_client.post(
        "/api/v1/search/hybrid",
        headers=auth_headers(user_id, tenant_id),
        json=payload,
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "failure",
    [
        QdrantIndexError("dense retrieval query failed"),
        OpenSearchIndexError("OpenSearch lexical query failed"),
    ],
)
def test_retrieval_service_failure_returns_503(
    search_client: TestClient,
    tenant_users: dict[str, tuple[UUID, UUID]],
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    tenant_id, user_id = tenant_users["a"]

    def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(search_api, "search_hybrid", fail)
    response = search_client.post(
        "/api/v1/search/hybrid",
        headers=auth_headers(user_id, tenant_id),
        json={"query": "revenue"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Hybrid retrieval service is unavailable"
