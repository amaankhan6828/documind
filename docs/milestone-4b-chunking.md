# Milestone 4B: Retrieval-Ready Chunking

Milestone 4B derives a canonical chunk representation from the persisted Docling document structure. Chunking runs inside the existing Celery `DOCUMENT_PROCESSING` job after pages, sections, and elements are persisted. Chunks are stored in PostgreSQL; there are no vector, search, or graph indexes in this milestone.

## Chunking strategy

Elements are processed in parser ordinal order. Adjacent text elements are grouped only while they share a section and fit within `CHUNK_MAX_CHARACTERS` (default `3000`). Oversized text elements are split near word boundaries and use `CHUNK_OVERLAP_CHARACTERS` (default `200`) between fragments. Overlap is limited to a single oversized text element; paragraph and section boundaries are preserved.

Tables become standalone chunks rendered from Docling cell values in row and column order, using tabs between cells and newlines between rows. A table is never split, even if it exceeds the configured character limit. Figures retain a neutral figure marker and a source-element link; no caption text is invented.

## Provenance and idempotency

Each chunk includes tenant, document, version, optional section, ordinal, text length, and a SHA-256 content hash. `document_chunk_sources` contains ordered links to actual `DocumentElement` rows and one row for each parser-reported source location. Page IDs/page numbers remain null where the source element has no page provenance. Source-element and page foreign keys include tenant, document, and version scope.

Chunk UUIDs are deterministic for a version, ordinal, and content hash. Reprocessing rebuilds that version's structure and chunks in the same transaction as setting its job to `COMPLETED`; existing chunks/source links are replaced. The worker's completed-job idempotency behavior remains unchanged.

## Configuration and limits

Set `CHUNK_MAX_CHARACTERS` and `CHUNK_OVERLAP_CHARACTERS` in the environment or root `.env`. Overlap must be nonnegative and smaller than the maximum. Length limits use characters, not model tokens. Empty or unusable elements do not create empty chunks.

Embeddings, retrieval indexes, reranking, graph data, and answer generation remain out of scope.
