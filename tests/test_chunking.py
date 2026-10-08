"""Deterministic chunk strategy and persistence coverage."""

import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.chunking.persistence import replace_document_chunks
from backend.app.chunking.service import build_chunks
from backend.app.models import (
    Document,
    DocumentChunk,
    DocumentChunkSource,
    DocumentElement,
    DocumentPage,
    DocumentSection,
    DocumentVersion,
    Tenant,
)


def create_version(session: Session, suffix: str) -> DocumentVersion:
    tenant = Tenant(name=f"Chunk tenant {suffix}", slug=f"chunk-tenant-{suffix}")
    session.add(tenant)
    session.flush()
    document = Document(tenant_id=tenant.id, title=f"Chunk document {suffix}")
    session.add(document)
    session.flush()
    version = DocumentVersion(
        tenant_id=tenant.id,
        document_id=document.id,
        version_number=1,
        original_filename="chunk-fixture.pdf",
        storage_key="stored-fixture.pdf",
    )
    session.add(version)
    session.flush()
    return version


def add_page(session: Session, version: DocumentVersion, page_number: int = 1) -> DocumentPage:
    page = DocumentPage(
        tenant_id=version.tenant_id,
        document_id=version.document_id,
        document_version_id=version.id,
        page_number=page_number,
    )
    session.add(page)
    session.flush()
    return page


def add_section(
    session: Session,
    version: DocumentVersion,
    page: DocumentPage,
    ordinal: int,
    source_ref: str,
    title: str,
) -> DocumentSection:
    section = DocumentSection(
        tenant_id=version.tenant_id,
        document_id=version.document_id,
        document_version_id=version.id,
        page_id=page.id,
        ordinal=ordinal,
        source_ref=source_ref,
        title=title,
        level=1,
        source_provenance=[{"page_number": page.page_number}],
    )
    session.add(section)
    session.flush()
    return section


def add_element(
    session: Session,
    version: DocumentVersion,
    page: DocumentPage | None,
    ordinal: int,
    source_ref: str,
    text: str | None,
    *,
    section: DocumentSection | None = None,
    element_type: str = "TEXT",
    structured_content: dict | None = None,
) -> DocumentElement:
    element = DocumentElement(
        tenant_id=version.tenant_id,
        document_id=version.document_id,
        document_version_id=version.id,
        page_id=page.id if page else None,
        section_id=section.id if section else None,
        ordinal=ordinal,
        source_ref=source_ref,
        element_type=element_type,
        text_content=text,
        structured_content=structured_content,
        source_provenance=(
            [{"page_number": page.page_number, "character_span": [0, len(text or "")]}]
            if page
            else []
        ),
    )
    session.add(element)
    session.flush()
    return element


def chunks_for(
    session: Session,
    version: DocumentVersion,
    *,
    max_characters: int = 3000,
    overlap_characters: int = 200,
):
    return build_chunks(
        version,
        session.scalars(
            select(DocumentElement)
            .where(DocumentElement.document_version_id == version.id)
            .order_by(DocumentElement.ordinal)
        ).all(),
        session.scalars(
            select(DocumentPage).where(DocumentPage.document_version_id == version.id)
        ).all(),
        max_characters=max_characters,
        overlap_characters=overlap_characters,
    )


def test_paragraphs_group_in_order_and_never_cross_sections(db_session: Session) -> None:
    version = create_version(db_session, "sections")
    first_page = add_page(db_session, version, 1)
    second_page = add_page(db_session, version, 2)
    section_a = add_section(db_session, version, first_page, 0, "#/texts/0", "A")
    section_b = add_section(db_session, version, second_page, 1, "#/texts/2", "B")
    add_element(db_session, version, first_page, 0, "#/texts/0", "Section A", section=section_a)
    add_element(db_session, version, first_page, 1, "#/texts/1", "First paragraph", section=section_a)
    add_element(db_session, version, second_page, 2, "#/texts/2", "Section B", section=section_b)
    add_element(db_session, version, second_page, 3, "#/texts/3", "Second paragraph", section=section_b)

    chunks = chunks_for(db_session, version)

    assert len(chunks) == 2
    assert chunks[0].text == "Section A\n\nFirst paragraph"
    assert chunks[1].text == "Section B\n\nSecond paragraph"
    assert chunks[0].section_id == section_a.id
    assert chunks[1].section_id == section_b.id
    assert [chunk.ordinal for chunk in chunks] == [0, 1]


def test_oversized_paragraph_splits_with_deterministic_overlap(db_session: Session) -> None:
    version = create_version(db_session, "overlap")
    page = add_page(db_session, version)
    element = add_element(
        db_session,
        version,
        page,
        0,
        "#/texts/0",
        "one two three four five six seven eight nine ten",
    )

    chunks = chunks_for(db_session, version, max_characters=18, overlap_characters=6)

    assert len(chunks) > 1
    assert all(chunk.character_length <= 18 for chunk in chunks)
    assert chunks[0].text.endswith("three")
    assert chunks[1].text.startswith("three")
    assert all(chunk.sources[0].document_element_id == element.id for chunk in chunks)
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))


def test_tables_are_first_class_and_never_split(db_session: Session) -> None:
    version = create_version(db_session, "table")
    page = add_page(db_session, version)
    add_element(db_session, version, page, 0, "#/texts/0", "Before table")
    table = add_element(
        db_session,
        version,
        page,
        1,
        "#/tables/0",
        None,
        element_type="TABLE",
        structured_content={
            "num_rows": 2,
            "num_cols": 2,
            "table_cells": [
                {"start_row_offset_idx": 0, "start_col_offset_idx": 0, "text": "Period"},
                {"start_row_offset_idx": 0, "start_col_offset_idx": 1, "text": "Revenue"},
                {"start_row_offset_idx": 1, "start_col_offset_idx": 0, "text": "FY2025"},
                {"start_row_offset_idx": 1, "start_col_offset_idx": 1, "text": "$42"},
            ],
        },
    )
    add_element(db_session, version, page, 2, "#/texts/1", "After table")

    chunks = chunks_for(db_session, version, max_characters=16, overlap_characters=2)

    table_chunks = [chunk for chunk in chunks if any(source.document_element_id == table.id for source in chunk.sources)]
    assert len(table_chunks) == 1
    assert table_chunks[0].text == "Period\tRevenue\nFY2025\t$42"
    assert table_chunks[0].character_length > 16


def test_content_hash_and_full_provenance_are_preserved(db_session: Session) -> None:
    version = create_version(db_session, "provenance")
    page = add_page(db_session, version, 3)
    section = add_section(db_session, version, page, 0, "#/texts/0", "Results")
    element = add_element(
        db_session, version, page, 0, "#/texts/1", "Revenue increased", section=section
    )

    chunk = chunks_for(db_session, version)[0]

    assert chunk.content_hash == hashlib.sha256(chunk.text.encode()).hexdigest()
    assert chunk.tenant_id == version.tenant_id
    assert chunk.document_id == version.document_id
    assert chunk.document_version_id == version.id
    assert chunk.section_id == section.id
    assert chunk.sources[0].document_element_id == element.id
    assert chunk.sources[0].source_ref == "#/texts/1"
    assert chunk.sources[0].page_id == page.id
    assert chunk.sources[0].page_number == 3


def test_rebuilding_chunks_is_idempotent(db_session: Session) -> None:
    version = create_version(db_session, "idempotent")
    page = add_page(db_session, version)
    add_element(db_session, version, page, 0, "#/texts/0", "Stable chunk text")
    chunks = chunks_for(db_session, version)

    replace_document_chunks(db_session, version, chunks)
    db_session.commit()
    chunk_id = chunks[0].chunk_id
    replace_document_chunks(db_session, version, chunks)
    db_session.commit()

    assert db_session.scalar(
        select(func.count()).select_from(DocumentChunk).where(
            DocumentChunk.document_version_id == version.id
        )
    ) == 1
    assert db_session.scalar(
        select(func.count()).select_from(DocumentChunkSource).where(
            DocumentChunkSource.document_version_id == version.id
        )
    ) == 1
    assert db_session.get(DocumentChunk, chunk_id) is not None


def test_empty_and_unusable_structure_produces_no_chunks(db_session: Session) -> None:
    version = create_version(db_session, "empty")
    page = add_page(db_session, version)
    add_element(db_session, version, page, 0, "#/texts/0", "   ")
    add_element(db_session, version, page, 1, "#/texts/1", None)
    add_element(
        db_session,
        version,
        page,
        2,
        "#/tables/0",
        None,
        element_type="TABLE",
        structured_content={"unsupported_shape": "no cells"},
    )
    assert chunks_for(db_session, version) == []


def test_chunk_source_cannot_cross_tenant_or_version(db_session: Session) -> None:
    version_a = create_version(db_session, "source-a")
    version_b = create_version(db_session, "source-b")
    page_a = add_page(db_session, version_a)
    source_element = add_element(
        db_session, version_a, page_a, 0, "#/texts/0", "Tenant A text"
    )
    chunk_a = chunks_for(db_session, version_a)[0]
    chunk_b_id = uuid4()
    # Create a valid B chunk row, then attempt to attach A's element under B's scope.
    db_session.add(
        DocumentChunk(
            id=chunk_b_id,
            tenant_id=version_b.tenant_id,
            document_id=version_b.document_id,
            document_version_id=version_b.id,
            ordinal=0,
            text=chunk_a.text,
            character_length=chunk_a.character_length,
            content_hash=chunk_a.content_hash,
        )
    )
    db_session.flush()
    db_session.add(
        DocumentChunkSource(
            chunk_id=chunk_b_id,
            source_ordinal=0,
            location_ordinal=0,
            tenant_id=version_b.tenant_id,
            document_id=version_b.document_id,
            document_version_id=version_b.id,
            document_element_id=source_element.id,
            page_id=None,
            page_number=None,
            source_ref=source_element.source_ref,
            element_type=source_element.element_type,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
