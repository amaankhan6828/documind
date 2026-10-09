"""Projection of PostgreSQL chunks into deterministic Qdrant dense points."""

from collections import defaultdict
from uuid import UUID

from qdrant_client import QdrantClient, models
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_session_factory
from backend.app.models import Document, DocumentChunk, DocumentChunkSource, DocumentVersion
from backend.app.retrieval.embeddings import EmbeddingProvider, get_embedding_provider
from backend.app.retrieval.qdrant import (
    QdrantIndexError,
    ensure_collection,
    reset_collection as reset_qdrant_collection,
)
from backend.app.retrieval.schemas import IndexRecord, SourceReference


def load_index_records(
    session: Session,
    *,
    batch_size: int,
    after_chunk_id: UUID | None = None,
    document_version_id: UUID | None = None,
) -> list[IndexRecord]:
    """Load a bounded, stable batch from canonical rows and their source links."""
    statement = (
        select(DocumentChunk, Document.document_type, Document.issuer)
        .join(Document, Document.id == DocumentChunk.document_id)
        .order_by(DocumentChunk.id)
        .limit(batch_size)
    )
    if after_chunk_id is not None:
        statement = statement.where(DocumentChunk.id > after_chunk_id)
    if document_version_id is not None:
        statement = statement.where(
            DocumentChunk.document_version_id == document_version_id
        )
    rows = list(session.execute(statement).all())
    if not rows:
        return []
    chunk_ids = [chunk.id for chunk, _doctype, _issuer in rows]
    source_rows = session.scalars(
        select(DocumentChunkSource)
        .where(DocumentChunkSource.chunk_id.in_(chunk_ids))
        .order_by(
            DocumentChunkSource.chunk_id,
            DocumentChunkSource.source_ordinal,
            DocumentChunkSource.location_ordinal,
        )
    ).all()
    sources_by_chunk: dict[UUID, list[SourceReference]] = defaultdict(list)
    for source in source_rows:
        sources_by_chunk[source.chunk_id].append(
            SourceReference(
                document_element_id=source.document_element_id,
                source_ref=source.source_ref,
                element_type=source.element_type,
                page_id=source.page_id,
                page_number=source.page_number,
            )
        )
    return [
        IndexRecord(
            chunk_id=chunk.id,
            tenant_id=chunk.tenant_id,
            document_id=chunk.document_id,
            document_version_id=chunk.document_version_id,
            section_id=chunk.section_id,
            ordinal=chunk.ordinal,
            text=chunk.text,
            content_hash=chunk.content_hash,
            document_type=document_type,
            issuer=issuer,
            sources=sources_by_chunk[chunk.id],
        )
        for chunk, document_type, issuer in rows
    ]


def payload_for(record: IndexRecord) -> dict:
    """Build compact filtering and provenance metadata without duplicating chunk text."""
    pages = []
    seen_pages: set[tuple[str | None, int | None]] = set()
    for source in record.sources:
        key = (str(source.page_id) if source.page_id else None, source.page_number)
        if key not in seen_pages and (key[0] is not None or key[1] is not None):
            seen_pages.add(key)
            pages.append({"page_id": key[0], "page_number": key[1]})
    payload = {
        "chunk_id": str(record.chunk_id),
        "tenant_id": str(record.tenant_id),
        "document_id": str(record.document_id),
        "document_version_id": str(record.document_version_id),
        "ordinal": record.ordinal,
        "content_hash": record.content_hash,
        "pages": pages,
        "sources": [
            {
                "document_element_id": str(source.document_element_id),
                "source_ref": source.source_ref,
                "element_type": source.element_type,
                "page_id": str(source.page_id) if source.page_id else None,
                "page_number": source.page_number,
            }
            for source in record.sources
        ],
    }
    if record.section_id is not None:
        payload["section_id"] = str(record.section_id)
    if record.document_type is not None:
        payload["document_type"] = record.document_type
    if record.issuer is not None:
        payload["issuer"] = record.issuer
    return payload


def index_records(
    records: list[IndexRecord],
    *,
    client: QdrantClient,
    embedder: EmbeddingProvider,
    settings: Settings,
) -> int:
    """Embed and upsert a bounded batch; canonical UUIDs are deterministic point IDs."""
    if not records:
        return 0
    if any(not record.text.strip() for record in records):
        raise ValueError("canonical chunks must contain non-blank text")
    vectors = embedder.embed([record.text for record in records])
    if len(vectors) != len(records):
        raise ValueError("embedding provider returned the wrong number of vectors")
    points = []
    for record, vector in zip(records, vectors, strict=True):
        if len(vector) != embedder.dimension:
            raise ValueError("embedding provider returned an unexpected vector dimension")
        points.append(
            models.PointStruct(
                id=str(record.chunk_id), vector=vector, payload=payload_for(record)
            )
        )
    ensure_collection(client, settings, dimension=embedder.dimension)
    try:
        client.upsert(
            collection_name=settings.qdrant_collection, points=points, wait=True
        )
    except Exception as exc:
        raise QdrantIndexError("Qdrant point upsert failed") from exc
    return len(points)


def index_document_version(
    document_version_id: UUID,
    *,
    session_factory: sessionmaker[Session] | None = None,
    client: QdrantClient | None = None,
    embedder: EmbeddingProvider | None = None,
    settings: Settings | None = None,
) -> int:
    """Index one version from committed canonical chunks in bounded batches."""
    selected_settings = settings or get_settings()
    selected_factory = session_factory or get_session_factory()
    selected_embedder = embedder or get_embedding_provider()
    if selected_embedder.dimension != selected_settings.embedding_dimension:
        raise ValueError("embedding provider dimension does not match configuration")
    if client is None:
        from backend.app.retrieval.qdrant import get_qdrant_client

        client = get_qdrant_client()
    with selected_factory() as session:
        tenant_id = session.scalar(
            select(DocumentVersion.tenant_id).where(
                DocumentVersion.id == document_version_id
            )
        )
    if tenant_id is None:
        return 0

    total = 0
    last_id: UUID | None = None
    current_chunk_ids: set[str] = set()
    while True:
        with selected_factory() as session:
            records = load_index_records(
                session,
                batch_size=selected_settings.index_batch_size,
                after_chunk_id=last_id,
                document_version_id=document_version_id,
            )
        if not records:
            break
        total += index_records(
            records, client=client, embedder=selected_embedder, settings=selected_settings
        )
        current_chunk_ids.update(str(record.chunk_id) for record in records)
        last_id = records[-1].chunk_id
    ensure_collection(
        client, selected_settings, dimension=selected_embedder.dimension
    )
    _delete_obsolete_points(
        client,
        settings=selected_settings,
        tenant_id=tenant_id,
        document_version_id=document_version_id,
        current_chunk_ids=current_chunk_ids,
    )
    return total


def _delete_obsolete_points(
    client: QdrantClient,
    *,
    settings: Settings,
    tenant_id: UUID,
    document_version_id: UUID,
    current_chunk_ids: set[str],
) -> None:
    """Delete only stale points for the successfully indexed version and tenant."""
    version_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="document_version_id",
                match=models.MatchValue(value=str(document_version_id)),
            ),
            models.FieldCondition(
                key="tenant_id",
                match=models.MatchValue(value=str(tenant_id)),
            ),
        ]
    )
    obsolete_point_ids: list[str] = []
    offset: int | str | UUID | None = None
    try:
        while True:
            points, next_offset = client.scroll(
                collection_name=settings.qdrant_collection,
                scroll_filter=version_filter,
                limit=256,
                offset=offset,
                with_payload=False,
            )
            obsolete_point_ids.extend(
                str(point.id)
                for point in points
                if str(point.id) not in current_chunk_ids
            )
            if next_offset is None:
                break
            offset = next_offset
    except Exception as exc:
        raise QdrantIndexError("could not find obsolete Qdrant points") from exc

    if not obsolete_point_ids:
        return
    try:
        client.delete(
            collection_name=settings.qdrant_collection,
            points_selector=models.PointIdsList(points=obsolete_point_ids),
            wait=True,
        )
    except Exception as exc:
        raise QdrantIndexError("could not delete obsolete Qdrant points") from exc


def rebuild_index(
    *,
    session_factory: sessionmaker[Session],
    client: QdrantClient,
    embedder: EmbeddingProvider,
    settings: Settings,
) -> int:
    """Drop/recreate derived Qdrant data and repopulate it from PostgreSQL."""
    reset_qdrant_collection(client, settings, dimension=embedder.dimension)
    total = 0
    last_id: UUID | None = None
    while True:
        with session_factory() as session:
            records = load_index_records(
                session, batch_size=settings.index_batch_size, after_chunk_id=last_id
            )
        if not records:
            return total
        total += index_records(records, client=client, embedder=embedder, settings=settings)
        last_id = records[-1].chunk_id
