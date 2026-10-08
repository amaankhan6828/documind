"""Transactional persistence helpers for canonical chunks and source links."""

from sqlalchemy import delete
from sqlalchemy.orm import Session

from backend.app.chunking.schemas import DocumentChunkData
from backend.app.models import DocumentChunk, DocumentChunkSource, DocumentVersion


def replace_document_chunks(
    session: Session,
    version: DocumentVersion,
    chunks: list[DocumentChunkData],
) -> None:
    """Replace all chunks for a version, retaining one transaction boundary."""
    session.execute(
        delete(DocumentChunkSource).where(
            DocumentChunkSource.document_version_id == version.id
        )
    )
    session.execute(
        delete(DocumentChunk).where(DocumentChunk.document_version_id == version.id)
    )
    session.add_all(
        DocumentChunk(
            id=chunk.chunk_id,
            tenant_id=chunk.tenant_id,
            document_id=chunk.document_id,
            document_version_id=chunk.document_version_id,
            section_id=chunk.section_id,
            ordinal=chunk.ordinal,
            text=chunk.text,
            character_length=chunk.character_length,
            content_hash=chunk.content_hash,
        )
        for chunk in chunks
    )
    session.flush()
    session.add_all(
        DocumentChunkSource(
            chunk_id=chunk.chunk_id,
            source_ordinal=source_ordinal,
            location_ordinal=source.location_ordinal,
            tenant_id=chunk.tenant_id,
            document_id=chunk.document_id,
            document_version_id=chunk.document_version_id,
            document_element_id=source.document_element_id,
            page_id=source.page_id,
            page_number=source.page_number,
            source_ref=source.source_ref,
            element_type=source.element_type,
        )
        for chunk in chunks
        for source_ordinal, source in enumerate(chunk.sources)
    )
    session.flush()
