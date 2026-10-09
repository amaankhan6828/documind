"""OpenSearch lexical projection tests and optional local-service integration."""

import hashlib
import os
from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from opensearchpy import OpenSearch
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings
from backend.app.db.base import Base
from backend.app.models import Document, DocumentChunk, DocumentVersion, Tenant
from backend.app.retrieval import opensearch
from backend.app.retrieval.lexical_indexing import (
    index_lexical_document_version,
    rebuild_opensearch_index,
)
from backend.app.retrieval.opensearch import (
    INDEX_BODY,
    OpenSearchIndexError,
    index_records,
    search_lexical,
)
from backend.app.retrieval.schemas import IndexRecord, SourceReference


class FakeIndices:
    def __init__(self, client: "FakeOpenSearch") -> None:
        self.client = client
        self.mappings: dict[str, dict] = {}

    def exists(self, *, index: str) -> bool:
        return index in self.mappings

    def create(self, *, index: str, body: dict) -> None:
        self.mappings[index] = deepcopy(body)
        self.client.documents[index] = {}

    def delete(self, *, index: str) -> None:
        self.mappings.pop(index, None)
        self.client.documents.pop(index, None)

    def refresh(self, *, index: str) -> None:
        assert index in self.mappings


class FakeOpenSearch:
    def __init__(self) -> None:
        self.indices = FakeIndices(self)
        self.documents: dict[str, dict[str, dict]] = {}
        self.search_calls: list[dict] = []
        self.events: list[tuple[str, str]] = []
        self.scrolls: dict[str, tuple[list[dict], int]] = {}
        self.next_scroll_id = 0
        self.bulk_failure: Exception | None = None

    def bulk(self, actions: list[dict], **_kwargs) -> tuple[int, list]:
        if self.bulk_failure is not None:
            raise self.bulk_failure
        for action in actions:
            operation = action["_op_type"]
            index = action["_index"]
            document_id = action["_id"]
            self.events.append((operation, document_id))
            if operation == "index":
                self.documents[index][document_id] = deepcopy(action["_source"])
            elif operation == "delete":
                self.documents[index].pop(document_id, None)
        return len(actions), []

    def search(self, *, index: str, body: dict, scroll: str | None = None) -> dict:
        self.search_calls.append(deepcopy(body))
        filters = body["query"]["bool"].get("filter", [])
        term_values = {
            next(iter(condition["term"].items()))
            for condition in filters
        }
        matching = []
        match_clause = body["query"]["bool"].get("must")
        query_terms = (
            match_clause[0]["match"]["text"].lower().split()
            if match_clause
            else []
        )
        all_docs = self.documents.get(index, {})
        doc_count = max(len(all_docs), 1)
        average_length = sum(
            len(document["text"].split()) for document in all_docs.values()
        ) / doc_count
        for document_id, source in all_docs.items():
            if any(
                source.get(field) != value
                for field, value in term_values
            ):
                continue
            score = 0.0
            if query_terms:
                tokens = source["text"].lower().split()
                frequencies = {token: tokens.count(token) for token in query_terms}
                if not any(frequencies.values()):
                    continue
                length = len(tokens)
                for term, frequency in frequencies.items():
                    if frequency:
                        inverse_frequency = 1 + (doc_count - 0.5) / (frequency + 0.5)
                        denominator = frequency + 1.2 * (
                            1 - 0.75 + 0.75 * length / max(average_length, 1)
                        )
                        score += inverse_frequency * (
                            frequency * (1.2 + 1) / denominator
                        )
            matching.append((score, document_id, source))
        matching.sort(key=lambda item: item[0], reverse=True)
        size = body.get("size", 10)
        if scroll:
            self.next_scroll_id += 1
            scroll_id = f"scroll-{self.next_scroll_id}"
            hits = [
                {
                    "_id": document_id,
                    "_source": source if body.get("_source") is not False else None,
                    "_score": score if query_terms else None,
                }
                for score, document_id, source in matching
            ]
            self.scrolls[scroll_id] = (hits, size)
            return {
                "_scroll_id": scroll_id,
                "hits": {"hits": hits[:size]},
            }
        return {
            "hits": {
                "hits": [
                    {
                        "_id": document_id,
                        "_source": deepcopy(source),
                        "_score": score,
                    }
                    for score, document_id, source in matching[:size]
                ]
            }
        }

    def scroll(self, *, scroll_id: str, scroll: str) -> dict:
        hits, offset = self.scrolls[scroll_id]
        size = 500
        self.scrolls[scroll_id] = (hits, offset + size)
        return {"_scroll_id": scroll_id, "hits": {"hits": hits[offset : offset + size]}}

    def clear_scroll(self, *, scroll_id: str) -> None:
        self.scrolls.pop(scroll_id, None)


def make_settings(index: str = "unit_lexical") -> Settings:
    return Settings(
        _env_file=None,
        jwt_secret_key="test-only-opensearch-secret-with-at-least-32-bytes",
        opensearch_url="http://localhost:9200",
        opensearch_index=index,
        index_batch_size=2,
    )


def make_record(
    *,
    tenant_id: UUID | None = None,
    document_id: UUID | None = None,
    document_version_id: UUID | None = None,
    text: str = "Revenue increased.",
) -> IndexRecord:
    return IndexRecord(
        chunk_id=uuid4(),
        tenant_id=tenant_id or uuid4(),
        document_id=document_id or uuid4(),
        document_version_id=document_version_id or uuid4(),
        section_id=uuid4(),
        ordinal=3,
        text=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        document_type="annual_report",
        issuer="Example Corp",
        sources=[
            SourceReference(
                document_element_id=uuid4(),
                source_ref="#/texts/8",
                element_type="TEXT",
                page_id=uuid4(),
                page_number=3,
            )
        ],
    )


def install_fake_bulk(monkeypatch: pytest.MonkeyPatch, client: FakeOpenSearch) -> None:
    def bulk(_client, actions, **kwargs):
        return client.bulk(list(actions), **kwargs)

    monkeypatch.setattr(opensearch.helpers, "bulk", bulk)


def make_canonical_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        tenant = Tenant(name="OpenSearch tenant", slug=f"opensearch-{uuid4().hex}")
        session.add(tenant)
        session.flush()
        document = Document(
            tenant_id=tenant.id,
            title="BM25 report",
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
        session.commit()
        identifiers = tenant.id, document.id, version.id
    return engine, factory, identifiers


def replace_canonical_chunks(
    factory: sessionmaker[Session],
    *,
    tenant_id: UUID,
    document_id: UUID,
    document_version_id: UUID,
    texts: list[str],
) -> list[str]:
    chunk_ids = [uuid4() for _ in texts]
    with factory() as session:
        session.execute(
            delete(DocumentChunk).where(
                DocumentChunk.document_version_id == document_version_id
            )
        )
        session.add_all(
            DocumentChunk(
                id=chunk_id,
                tenant_id=tenant_id,
                document_id=document_id,
                document_version_id=document_version_id,
                ordinal=ordinal,
                text=text,
                character_length=len(text),
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
            )
            for ordinal, (chunk_id, text) in enumerate(zip(chunk_ids, texts, strict=True))
        )
        session.commit()
    return [str(chunk_id) for chunk_id in chunk_ids]


def test_mapping_and_indexed_fields_preserve_chunk_text_and_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeOpenSearch()
    install_fake_bulk(monkeypatch, client)
    settings = make_settings()
    record = make_record()

    assert index_records([record], client=client, settings=settings) == 1

    mapping = client.indices.mappings[settings.opensearch_index]
    properties = mapping["mappings"]["properties"]
    assert mapping["mappings"]["dynamic"] == "strict"
    assert properties["text"] == {
        "type": "text",
        "similarity": "documind_bm25",
    }
    assert mapping["settings"]["similarity"]["documind_bm25"]["type"] == "BM25"
    assert properties["tenant_id"] == {"type": "keyword"}
    assert properties["sources"]["type"] == "nested"
    indexed = client.documents[settings.opensearch_index][str(record.chunk_id)]
    assert indexed == record.model_dump(mode="json")
    assert indexed["text"] == record.text
    assert indexed["content_hash"] == record.content_hash
    assert indexed["document_type"] == "annual_report"
    assert indexed["issuer"] == "Example Corp"
    assert indexed["sources"][0]["source_ref"] == "#/texts/8"
    assert indexed["sources"][0]["page_number"] == 3
    assert INDEX_BODY["mappings"]["properties"]["section_id"]["type"] == "keyword"


def test_repeated_bulk_indexing_is_idempotent_and_uses_chunk_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeOpenSearch()
    install_fake_bulk(monkeypatch, client)
    record = make_record()

    assert index_records([record], client=client, settings=make_settings()) == 1
    assert index_records([record], client=client, settings=make_settings()) == 1

    assert list(client.documents["unit_lexical"]) == [str(record.chunk_id)]
    assert [event[0] for event in client.events] == ["index", "index"]


def test_lexical_search_filters_tenant_and_returns_bm25_ranked_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeOpenSearch()
    install_fake_bulk(monkeypatch, client)
    tenant_id = uuid4()
    comprehensive = make_record(
        tenant_id=tenant_id,
        text="Revenue margin increased revenue margin",
    )
    single_term = make_record(tenant_id=tenant_id, text="Revenue outlook")
    another_tenant = make_record(
        tenant_id=uuid4(),
        text="Revenue margin confidential",
    )
    index_records(
        [comprehensive, single_term, another_tenant],
        client=client,
        settings=make_settings(),
    )

    hits = search_lexical(
        tenant_id,
        "revenue margin",
        5,
        client=client,
        settings=make_settings(),
    )

    assert [hit.chunk_id for hit in hits] == [
        comprehensive.chunk_id,
        single_term.chunk_id,
    ]
    assert hits[0].payload["sources"][0]["source_ref"] == "#/texts/8"
    assert hits[0].payload["sources"][0]["page_number"] == 3
    query = client.search_calls[-1]["query"]["bool"]
    assert {"term": {"tenant_id": str(tenant_id)}} in query["filter"]
    assert query["must"] == [{"match": {"text": "revenue margin"}}]
    assert hits[0].score > hits[1].score


def test_lexical_search_returns_empty_results_and_validates_tenant_query() -> None:
    client = FakeOpenSearch()
    settings = make_settings()

    assert search_lexical(
        uuid4(), "missing terms", 10, client=client, settings=settings
    ) == []
    with pytest.raises(ValueError, match="tenant ID"):
        search_lexical(None, "revenue", 10, client=client, settings=settings)
    with pytest.raises(ValueError, match="blank"):
        search_lexical(uuid4(), " ", 10, client=client, settings=settings)


def test_version_indexing_reconciles_only_exact_tenant_and_version_after_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, factory, (tenant_id, document_id, version_id) = make_canonical_factory()
    client = FakeOpenSearch()
    install_fake_bulk(monkeypatch, client)
    settings = make_settings()
    try:
        first_ids = replace_canonical_chunks(
            factory,
            tenant_id=tenant_id,
            document_id=document_id,
            document_version_id=version_id,
            texts=["Keep this chunk", "Remove this chunk"],
        )
        assert index_lexical_document_version(
            version_id,
            session_factory=factory,
            client=client,
            settings=settings,
        ) == 2
        other_tenant_record = make_record(text="Other tenant record")
        other_version_record = make_record(
            tenant_id=tenant_id,
            text="Other version record",
        )
        index_records(
            [other_tenant_record, other_version_record],
            client=client,
            settings=settings,
        )
        second_ids = replace_canonical_chunks(
            factory,
            tenant_id=tenant_id,
            document_id=document_id,
            document_version_id=version_id,
            texts=["Keep this chunk"],
        )
        client.events.clear()

        assert index_lexical_document_version(
            version_id,
            session_factory=factory,
            client=client,
            settings=settings,
        ) == 1

        records = client.documents[settings.opensearch_index]
        assert set(second_ids) <= set(records)
        assert first_ids[1] not in records
        assert str(other_tenant_record.chunk_id) in records
        assert str(other_version_record.chunk_id) in records
        delete_position = next(
            index
            for index, event in enumerate(client.events)
            if event[0] == "delete"
        )
        assert all(
            event[0] == "index" for event in client.events[:delete_position]
        )
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_zero_current_chunks_remove_all_records_in_only_that_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, factory, (tenant_id, document_id, version_id) = make_canonical_factory()
    client = FakeOpenSearch()
    install_fake_bulk(monkeypatch, client)
    settings = make_settings()
    try:
        old_ids = replace_canonical_chunks(
            factory,
            tenant_id=tenant_id,
            document_id=document_id,
            document_version_id=version_id,
            texts=["Obsolete"],
        )
        index_lexical_document_version(
            version_id,
            session_factory=factory,
            client=client,
            settings=settings,
        )
        other = make_record(text="Protected unrelated record")
        index_records([other], client=client, settings=settings)
        replace_canonical_chunks(
            factory,
            tenant_id=tenant_id,
            document_id=document_id,
            document_version_id=version_id,
            texts=[],
        )

        assert index_lexical_document_version(
            version_id,
            session_factory=factory,
            client=client,
            settings=settings,
        ) == 0
        records = client.documents[settings.opensearch_index]
        assert old_ids[0] not in records
        assert str(other.chunk_id) in records
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_zero_chunk_version_creates_index_before_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, factory, (_tenant_id, _document_id, version_id) = make_canonical_factory()
    client = FakeOpenSearch()
    install_fake_bulk(monkeypatch, client)
    settings = make_settings()
    try:
        assert index_lexical_document_version(
            version_id,
            session_factory=factory,
            client=client,
            settings=settings,
        ) == 0
        assert client.indices.exists(index=settings.opensearch_index)
        assert client.documents[settings.opensearch_index] == {}
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_failed_bulk_write_does_not_reconcile_existing_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, factory, (tenant_id, document_id, version_id) = make_canonical_factory()
    client = FakeOpenSearch()
    install_fake_bulk(monkeypatch, client)
    settings = make_settings()
    try:
        replace_canonical_chunks(
            factory,
            tenant_id=tenant_id,
            document_id=document_id,
            document_version_id=version_id,
            texts=["Current canonical"],
        )
        index_lexical_document_version(
            version_id,
            session_factory=factory,
            client=client,
            settings=settings,
        )
        stale = make_record(
            tenant_id=tenant_id,
            document_id=document_id,
            document_version_id=version_id,
            text="Stale record",
        )
        index_records([stale], client=client, settings=settings)
        client.events.clear()
        client.bulk_failure = ConnectionError("OpenSearch unavailable")
        replace_canonical_chunks(
            factory,
            tenant_id=tenant_id,
            document_id=document_id,
            document_version_id=version_id,
            texts=["Replacement canonical"],
        )

        with pytest.raises(OpenSearchIndexError, match="bulk index failed"):
            index_lexical_document_version(
                version_id,
                session_factory=factory,
                client=client,
                settings=settings,
            )

        assert not any(event[0] == "delete" for event in client.events)
        assert str(stale.chunk_id) in client.documents[settings.opensearch_index]
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_rebuild_recreates_index_from_canonical_database_in_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, factory, (tenant_id, document_id, version_id) = make_canonical_factory()
    client = FakeOpenSearch()
    install_fake_bulk(monkeypatch, client)
    settings = make_settings()
    try:
        ids = replace_canonical_chunks(
            factory,
            tenant_id=tenant_id,
            document_id=document_id,
            document_version_id=version_id,
            texts=["First canonical chunk", "Second canonical chunk", "Third canonical chunk"],
        )
        stale = make_record(text="Not in PostgreSQL")
        index_records([stale], client=client, settings=settings)

        total = rebuild_opensearch_index(
            session_factory=factory,
            client=client,
            settings=settings,
        )

        assert total == 3
        assert set(client.documents[settings.opensearch_index]) == set(ids)
        assert str(stale.chunk_id) not in client.documents[settings.opensearch_index]
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_real_local_opensearch_indexing_cleanup_search_and_rebuild() -> None:
    """Exercises the real local service when Docker Compose OpenSearch is available."""
    url = get_local_opensearch_url()
    client = OpenSearch(hosts=[url], timeout=1)
    try:
        client.info()
    except Exception:
        pytest.skip("local Compose OpenSearch is not running")
    engine, factory, (tenant_id, document_id, version_id) = make_canonical_factory()
    settings = make_settings(f"documind_test_{uuid4().hex[:12]}")
    try:
        original_ids = replace_canonical_chunks(
            factory,
            tenant_id=tenant_id,
            document_id=document_id,
            document_version_id=version_id,
            texts=[
                "Revenue margin revenue margin.",
                "Revenue margin.",
            ],
        )
        assert index_lexical_document_version(
            version_id,
            session_factory=factory,
            client=client,
            settings=settings,
        ) == 2
        hits = search_lexical(
            tenant_id,
            "revenue margin",
            10,
            client=client,
            settings=settings,
        )
        assert {str(hit.chunk_id) for hit in hits} == set(original_ids)
        assert str(hits[0].chunk_id) == original_ids[0]
        assert hits[0].payload["content_hash"]

        current_ids = replace_canonical_chunks(
            factory,
            tenant_id=tenant_id,
            document_id=document_id,
            document_version_id=version_id,
            texts=["Revenue margin improved in the current canonical version."],
        )
        assert index_lexical_document_version(
            version_id,
            session_factory=factory,
            client=client,
            settings=settings,
        ) == 1
        assert {str(hit.chunk_id) for hit in search_lexical(
            tenant_id, "revenue margin", 10, client=client, settings=settings
        )} == set(current_ids)
        assert rebuild_opensearch_index(
            session_factory=factory,
            client=client,
            settings=settings,
        ) == 1
        assert {str(hit.chunk_id) for hit in search_lexical(
            tenant_id, "revenue margin", 10, client=client, settings=settings
        )} == set(current_ids)
    finally:
        if client.indices.exists(index=settings.opensearch_index):
            client.indices.delete(index=settings.opensearch_index)
        Base.metadata.drop_all(engine)
        engine.dispose()


def get_local_opensearch_url() -> str:
    return os.environ.get("OPENSEARCH_URL", "http://localhost:9200")
