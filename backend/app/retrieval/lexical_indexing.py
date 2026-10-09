"""PostgreSQL-to-OpenSearch projection and rebuild orchestration."""

from uuid import UUID

from opensearchpy import OpenSearch
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings, get_settings
from backend.app.db.session import get_session_factory
from backend.app.models import DocumentVersion
from backend.app.retrieval.indexing import load_index_records
from backend.app.retrieval.opensearch import (
    OpenSearchIndexError,
    delete_obsolete_records,
    ensure_index,
    index_records,
    refresh_index,
    reset_index,
)


def index_lexical_document_version(
    document_version_id: UUID,
    *,
    session_factory: sessionmaker[Session] | None = None,
    client: OpenSearch | None = None,
    settings: Settings | None = None,
) -> int:
    """Index a committed version completely, then reconcile its stale records."""
    from backend.app.retrieval.opensearch import get_opensearch_client

    selected_settings = settings or get_settings()
    selected_factory = session_factory or get_session_factory()
    selected_client = client or get_opensearch_client()
    with selected_factory() as session:
        tenant_id = session.scalar(
            select(DocumentVersion.tenant_id).where(
                DocumentVersion.id == document_version_id
            )
        )
    if tenant_id is None:
        return 0

    ensure_index(selected_client, selected_settings)
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
        if any(
            record.tenant_id != tenant_id
            or record.document_version_id != document_version_id
            for record in records
        ):
            raise OpenSearchIndexError(
                "canonical chunk scope does not match its document version"
            )
        total += index_records(
            records, client=selected_client, settings=selected_settings
        )
        current_chunk_ids.update(str(record.chunk_id) for record in records)
        last_id = records[-1].chunk_id

    delete_obsolete_records(
        selected_client,
        settings=selected_settings,
        tenant_id=tenant_id,
        document_version_id=document_version_id,
        current_chunk_ids=current_chunk_ids,
    )
    return total


def rebuild_opensearch_index(
    *,
    session_factory: sessionmaker[Session],
    client: OpenSearch,
    settings: Settings,
) -> int:
    """Recreate OpenSearch from bounded reads of canonical PostgreSQL chunks."""
    reset_index(client, settings)
    total = 0
    last_id: UUID | None = None
    while True:
        with session_factory() as session:
            records = load_index_records(
                session, batch_size=settings.index_batch_size, after_chunk_id=last_id
            )
        if not records:
            refresh_index(client, settings)
            return total
        total += index_records(records, client=client, settings=settings)
        last_id = records[-1].chunk_id
