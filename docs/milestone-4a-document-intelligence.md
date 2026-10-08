# Milestone 4A: Docling Document Intelligence

Milestone 4A replaces the Milestone 3 placeholder operation with asynchronous Docling conversion of the PDF/DOCX file belonging to the queued `DocumentVersion`. The HTTP upload endpoint remains asynchronous and unchanged: it stores the file/version, creates a `ProcessingJob`, and enqueues the worker.

## Conversion boundary

`backend/app/document_intelligence/docling_adapter.py` is the only module that imports Docling APIs. It allows only PDF and DOCX input paths and maps Docling output into the internal Pydantic schemas. The worker resolves the server-generated storage key under the configured storage root, checks the recorded size and SHA-256, and never accepts a client path or remote URL.

The adapter normalizes parser-reported pages, section headings, text items, tables, and figures. Docling references, page numbers, bounding boxes, and character spans are carried through where present. It does not fabricate page numbers. Picture binaries are not duplicated in PostgreSQL; figure records retain source references and extracted captions/references.

## PDF and OCR behavior

Selectable-text PDFs use Docling's `NativePdfPipeline`, which reads the PDF's native text cells and provenance without loading OCR, layout, or table models. If that pass finds no text, the adapter retries with Docling's standard PDF pipeline configured with `TesseractCliOcrOptions`, English trained data, OCR enabled, and table-structure inference disabled. This avoids RapidOCR/ModelScope downloads. Tesseract is a local system executable (`brew install tesseract` on macOS; the backend container installs `tesseract-ocr`). The OCR fallback still uses Docling's layout model, whose artifacts may need to be available locally or downloaded on first use; basic selectable-text ingestion does not require those model artifacts. The small offline PDF tests exercise the native path and the OCR fallback selection/configuration without network access.

## Canonical storage and provenance

PostgreSQL remains authoritative. Migration `20261007_0006` adds `structure_metadata` to `document_versions` and creates:

- `document_pages` for reported page numbers and optional dimensions.
- `document_sections` for heading hierarchy and parser references.
- `document_elements` for text, table, and figure content.

Every row carries tenant, document, and document-version identity. Composite foreign keys prevent a page, section, element, or parent section from crossing a tenant or version. An element links directly to a page when one is available and retains the complete parser provenance list for additional locations.

## Worker behavior

The worker validates local file containment and integrity before conversion. It updates coarse progress at conversion/persistence boundaries; percentages indicate job stages, not a measured share of document work. Canonical rows and the job's `COMPLETED` state commit in the same transaction. Repeated task delivery for a completed job remains a no-op. Failed conversion produces a generic user-safe error; parser details and uploaded text are not returned through the API.

The Docker Compose worker mounts the same configured local document storage directory as the API. Local storage remains a development deployment choice; object storage is not part of this milestone.

## Current limitations

This milestone does not create chunks, embeddings, search indexes, graph entities, summaries, or generated answers. The worker processes one whole document version synchronously per Celery task. Image-only PDF conversion requires local Tesseract and Docling layout artifacts; this environment could not validate that full fallback without those model artifacts.
