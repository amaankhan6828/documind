"""Structural, deterministic chunk construction with parser provenance."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from uuid import NAMESPACE_URL, UUID, uuid5

from backend.app.chunking.schemas import ChunkSourceData, DocumentChunkData
from backend.app.models import DocumentElement, DocumentPage, DocumentVersion


def build_chunks(
    version: DocumentVersion,
    elements: Iterable[DocumentElement],
    pages: Iterable[DocumentPage],
    *,
    max_characters: int,
    overlap_characters: int,
) -> list[DocumentChunkData]:
    """Group neighboring content within sections and split oversized text only."""
    if max_characters < 1:
        raise ValueError("max_characters must be positive")
    if overlap_characters < 0 or overlap_characters >= max_characters:
        raise ValueError("overlap_characters must be nonnegative and smaller than max")

    page_ids = {page.page_number: page.id for page in pages}
    ordered = sorted(elements, key=lambda element: (element.ordinal, str(element.id)))
    chunks: list[DocumentChunkData] = []
    pending_text: list[str] = []
    pending_elements: list[DocumentElement] = []
    pending_section_id: UUID | None = None
    pending_section_set = False

    def flush_pending() -> None:
        nonlocal pending_text, pending_elements, pending_section_id, pending_section_set
        if pending_text and pending_elements:
            _append_chunk(
                chunks,
                version,
                "\n\n".join(pending_text),
                pending_elements,
                page_ids,
                pending_section_id,
            )
        pending_text = []
        pending_elements = []
        pending_section_id = None
        pending_section_set = False

    for element in ordered:
        text = _element_text(element)
        if not text:
            continue

        if pending_section_set and element.section_id != pending_section_id:
            flush_pending()

        if element.element_type in {"TABLE", "FIGURE"}:
            flush_pending()
            _append_chunk(chunks, version, text, [element], page_ids, element.section_id)
            continue

        if len(text) > max_characters:
            flush_pending()
            for piece in _split_text(text, max_characters, overlap_characters):
                _append_chunk(chunks, version, piece, [element], page_ids, element.section_id)
            continue

        projected_length = len("\n\n".join([*pending_text, text]))
        if pending_text and projected_length > max_characters:
            flush_pending()
        pending_text.append(text)
        pending_elements.append(element)
        pending_section_id = element.section_id
        pending_section_set = True

    flush_pending()
    return chunks


def _element_text(element: DocumentElement) -> str:
    if element.element_type == "TABLE":
        return _table_text(element.structured_content)
    if element.element_type == "FIGURE":
        # Figure captions are represented by their own source elements where
        # available. Keep this figure record searchable without inventing text.
        return "Figure"
    return (element.text_content or "").strip()


def _table_text(structured_content: dict | None) -> str:
    """Render Docling table cell values in row/column order without rewriting."""
    if not structured_content:
        return ""
    raw_cells = structured_content.get("table_cells", [])
    if not isinstance(raw_cells, list) or not raw_cells:
        return ""

    cells: dict[tuple[int, int], str] = {}
    max_row = -1
    max_column = -1
    for cell in raw_cells:
        if not isinstance(cell, dict):
            continue
        try:
            row = int(cell["start_row_offset_idx"])
            column = int(cell["start_col_offset_idx"])
            value = str(cell.get("text", ""))
        except (KeyError, TypeError, ValueError):
            continue
        if row < 0 or column < 0:
            continue
        cells[(row, column)] = value
        max_row = max(max_row, row)
        max_column = max(max_column, column)

    if max_row < 0 or max_column < 0:
        return ""
    return "\n".join(
        "\t".join(cells.get((row, column), "") for column in range(max_column + 1))
        for row in range(max_row + 1)
    ).strip()


def _split_text(text: str, max_characters: int, overlap_characters: int) -> list[str]:
    """Split long text near word boundaries and overlap adjacent fragments."""
    pieces: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + max_characters, length)
        if end < length:
            boundary = text.rfind(" ", start + max_characters // 2, end)
            if boundary > start:
                end = boundary
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= length:
            break
        next_start = max(start + 1, end - overlap_characters)
        while next_start < end and not text[next_start - 1].isspace():
            next_start += 1
        if next_start >= end:
            next_start = end
        start = next_start
    return pieces


def _append_chunk(
    chunks: list[DocumentChunkData],
    version: DocumentVersion,
    text: str,
    elements: list[DocumentElement],
    page_ids: dict[int, UUID],
    section_id: UUID | None,
) -> None:
    normalized_text = text.strip()
    if not normalized_text:
        return
    ordinal = len(chunks)
    digest = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
    chunk_id = uuid5(
        NAMESPACE_URL,
        f"documind:{version.id}:{ordinal}:{digest}",
    )
    sources = [
        source
        for element in elements
        for source in _element_sources(element, page_ids)
    ]
    chunks.append(
        DocumentChunkData(
            chunk_id=chunk_id,
            tenant_id=version.tenant_id,
            document_id=version.document_id,
            document_version_id=version.id,
            section_id=section_id,
            ordinal=ordinal,
            text=normalized_text,
            character_length=len(normalized_text),
            content_hash=digest,
            sources=sources,
        )
    )


def _element_sources(
    element: DocumentElement,
    page_ids: dict[int, UUID],
) -> list[ChunkSourceData]:
    locations = element.source_provenance or []
    if not locations:
        locations = [{}]
    sources: list[ChunkSourceData] = []
    for location_ordinal, location in enumerate(locations):
        page_number = location.get("page_number")
        if not isinstance(page_number, int) or page_number <= 0:
            page_number = None
        sources.append(
            ChunkSourceData(
                document_element_id=element.id,
                source_ref=element.source_ref,
                element_type=element.element_type,
                element_ordinal=element.ordinal,
                location_ordinal=location_ordinal,
                page_id=page_ids.get(page_number) if page_number is not None else None,
                page_number=page_number,
            )
        )
    return sources
