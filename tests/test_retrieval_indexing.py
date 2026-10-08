"""Deterministic Qdrant projection tests plus optional local-service integration."""

import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from qdrant_client import QdrantClient, models
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.chunking.persistence import replace_document_chunks
from backend.app.chunking.schemas import ChunkSourceData, DocumentChunkData
from backend.app.core.config import Settings
from backend.app.db.base import Base
from backend.app.models import (
    Document,
    DocumentChunk,
    DocumentElement,
    DocumentPage,
    DocumentVersion,
    Tenant,
)
from backend.app.retrieval.indexing import (
    index_records,
    load_index_records,
    payload_for,
    rebuild_index,
)
from backend.app.retrieval.qdrant import (
    QdrantIndexError,
    ensure_collection,
    search_dense,
)
from backend.app.retrieval.schemas import IndexRecord, SourceReference


class FakeEmbedder:
    dimension = 3

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [float((sum(text.encode()) + offset) % 17 + 1) for offset in range(3)]
            magnitude = sum(value * value for value in vector) ** 0.5
            vectors.append([value / magnitude for value in vector])
        return vectors


class FakeQdrant:
    def __init__(self) -> None:
        self.exists = False
        self.points: dict[str, models.PointStruct] = {}
        self.upsert_calls: list[list[models.PointStruct]] = []

    def collection_exists(self, _name: str) -> bool:
        return self.exists

    def create_collection(self, **kwargs) -> bool:
        self.exists = True
        self.vector_size = kwargs["vectors_config"].size
        return True

    def get_collection(self, _name: str):
        return SimpleNamespace(
            config=SimpleNamespace(
                params=SimpleNamespace(
                    vectors=models.VectorParams(
                        size=self.vector_size, distance=models.Distance.COSINE
                    )
                )
            )
        )

    def create_payload_index(self, **_kwargs) -> bool:
        return True

    def delete_collection(self, _name: str) -> bool:
        self.exists = False
        self.points.clear()
        return True

    def upsert(self, *, points, **_kwargs) -> bool:
        self.upsert_calls.append(points)
        self.points.update({str(point.id): point for point in points})
        return True

    def query_points(self, *, query_filter, limit, **_kwargs):
        tenant_id = query_filter.must[0].match.value
        matching = [
            point
            for point in self.points.values()
            if point.payload["tenant_id"] == tenant_id
        ]
        return SimpleNamespace(
            points=[
                SimpleNamespace(id=point.id, score=0.9, payload=point.payload)
                for point in matching[:limit]
            ]
        )


def make_settings(collection: str = "unit_chunks") -> Settings:
    return Settings(
        _env_file=None,
        jwt_secret_key="test-only-qdrant-secret-with-at-least-32-bytes",
        qdrant_url="http://localhost:6333",
        qdrant_collection=collection,
        embedding_model="local-test-model",
        embedding_dimension=3,
        index_batch_size=2,
    )


def make_record(*, tenant_id=None, text: str = "Revenue was 42") -> IndexRecord:
    tenant_id = tenant_id or uuid4()
    element_id, page_id = uuid4(), uuid4()
    return IndexRecord(
        chunk_id=uuid4(),
        tenant_id=tenant_id,
        document_id=uuid4(),
        document_version_id=uuid4(),
        section_id=uuid4(),
        ordinal=4,
        text=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        document_type="annual_report",
        issuer="Example Corp",
        sources=[
            SourceReference(
                document_element_id=element_id,
                source_ref="#/texts/8",
                element_type="TEXT",
                page_id=page_id,
                page_number=3,
            )
        ],
    )


def test_collection_is_created_with_configured_dimension_and_cosine() -> None:
    client = FakeQdrant()
    settings = make_settings()

    ensure_collection(client, settings)

    assert client.exists
    assert client.vector_size == 3


def test_index_uses_canonical_uuid_and_compact_provenance_payload() -> None:
    record = make_record()
    client = FakeQdrant()
    settings = make_settings()
    embedder = FakeEmbedder()

    assert index_records([record], client=client, embedder=embedder, settings=settings) == 1
    assert index_records([record], client=client, embedder=embedder, settings=settings) == 1

    assert len(client.points) == 1
    point = client.points[str(record.chunk_id)]
    assert point.payload == payload_for(record)
    assert point.payload["tenant_id"] == str(record.tenant_id)
    assert point.payload["document_version_id"] == str(record.document_version_id)
    assert point.payload["section_id"] == str(record.section_id)
    assert point.payload["pages"] == [
        {"page_id": str(record.sources[0].page_id), "page_number": 3}
    ]
    assert point.payload["sources"][0]["source_ref"] == "#/texts/8"
    assert len(client.upsert_calls) == 2
    assert client.upsert_calls[0][0].id == client.upsert_calls[1][0].id


def test_dense_search_filters_by_tenant_and_returns_ranked_candidates() -> None:
    tenant_a, tenant_b = uuid4(), uuid4()
    client = FakeQdrant()
    settings = make_settings()
    embedder = FakeEmbedder()
    records = [make_record(tenant_id=tenant_a), make_record(tenant_id=tenant_b)]
    index_records(records, client=client, embedder=embedder, settings=settings)

    hits = search_dense(
        tenant_a,
        "revenue",
        3,
        client=client,
        embedder=embedder,
        settings=settings,
    )

    assert len(hits) == 1
    assert hits[0].chunk_id == records[0].chunk_id
    assert hits[0].payload["tenant_id"] == str(tenant_a)


def test_empty_and_malformed_batches_are_handled() -> None:
    client = FakeQdrant()
    embedder = FakeEmbedder()
    settings = make_settings()
    assert index_records([], client=client, embedder=embedder, settings=settings) == 0
    malformed = make_record(text="   ")
    with pytest.raises(ValueError, match="non-blank"):
        index_records([malformed], client=client, embedder=embedder, settings=settings)
    assert client.points == {}


def test_qdrant_write_failure_is_reported_as_retryable_index_failure() -> None:
    class FailingQdrant(FakeQdrant):
        def upsert(self, **_kwargs):
            raise ConnectionError("temporary Qdrant outage")

    with pytest.raises(QdrantIndexError, match="upsert failed"):
        index_records(
            [make_record()],
            client=FailingQdrant(),
            embedder=FakeEmbedder(),
            settings=make_settings(),
        )


def test_rebuild_reads_postgres_chunks_in_batches_and_reuses_ids() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory() as session:
            tenant = Tenant(name="Index tenant", slug="index-tenant")
            session.add(tenant)
            session.flush()
            document = Document(
                tenant_id=tenant.id,
                title="Indexed report",
                document_type="annual_report",
                issuer="Example Corp",
            )
            session.add(document)
            session.flush()
            version = DocumentVersion(
                tenant_id=tenant.id,
                document_id=document.id,
                version_number=1,
            )
            session.add(version)
            session.flush()
            page = DocumentPage(
                tenant_id=tenant.id,
                document_id=document.id,
                document_version_id=version.id,
                page_number=1,
            )
            session.add(page)
            session.flush()
            element = DocumentElement(
                tenant_id=tenant.id,
                document_id=document.id,
                document_version_id=version.id,
                page_id=page.id,
                ordinal=0,
                source_ref="#/texts/0",
                element_type="TEXT",
                text_content="Canonical revenue is 42.",
                source_provenance=[{"page_number": 1}],
            )
            session.add(element)
            session.flush()
            canonical = DocumentChunkData(
                chunk_id=uuid4(),
                tenant_id=tenant.id,
                document_id=document.id,
                document_version_id=version.id,
                ordinal=0,
                text="Canonical revenue is 42.",
                character_length=len("Canonical revenue is 42."),
                content_hash=hashlib.sha256(b"Canonical revenue is 42.").hexdigest(),
                sources=[
                    ChunkSourceData(
                        document_element_id=element.id,
                        source_ref="#/texts/0",
                        element_type="TEXT",
                        element_ordinal=0,
                        location_ordinal=0,
                        page_id=page.id,
                        page_number=1,
                    )
                ],
            )
            replace_document_chunks(session, version, [canonical])
            session.commit()

        client = FakeQdrant()
        settings = make_settings()
        count = rebuild_index(
            session_factory=factory,
            client=client,
            embedder=FakeEmbedder(),
            settings=settings,
        )
        assert count == 1
        assert str(canonical.chunk_id) in client.points
        assert client.points[str(canonical.chunk_id)].payload["sources"][0]["page_number"] == 1
        with factory() as session:
            loaded = load_index_records(session, batch_size=2)
            assert loaded[0].text == "Canonical revenue is 42."
            assert loaded[0].sources[0].document_element_id == element.id
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_real_local_qdrant_collection_and_tenant_filter() -> None:
    """Runs against Compose Qdrant when available; no network or hosted model used."""
    client = QdrantClient(url="http://localhost:6333", timeout=1)
    try:
        client.get_collections()
    except Exception:
        pytest.skip("local Compose Qdrant is not running")
    settings = make_settings(f"documind_test_{uuid4().hex[:12]}")
    records = [make_record(), make_record()]
    try:
        count = index_records(
            records,
            client=client,
            embedder=FakeEmbedder(),
            settings=settings,
        )
        assert count == 2
        result = search_dense(
            records[0].tenant_id,
            "revenue",
            5,
            client=client,
            embedder=FakeEmbedder(),
            settings=settings,
        )
        assert result
        assert all(hit.payload["tenant_id"] == str(records[0].tenant_id) for hit in result)
        assert result[0].payload["sources"][0]["document_element_id"] == str(
            records[0].sources[0].document_element_id
        )
    finally:
        if client.collection_exists(settings.qdrant_collection):
            client.delete_collection(settings.qdrant_collection)
