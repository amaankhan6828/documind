# Milestone 2C: Secure File Upload

DocuMind accepts authenticated PDF and DOCX files for an existing document through `POST /api/v1/documents/{document_id}/versions`, with the multipart field named `file`. The document is looked up by both its ID and the authenticated user's tenant ID. A cross-tenant or missing document returns `404`.

## File handling

- Only `.pdf` with `application/pdf` and `.docx` with the Office Open XML MIME type are accepted. The implementation checks the PDF signature or the DOCX ZIP structure and required document entries; it does not extract archive contents.
- `MAX_UPLOAD_SIZE_MB` sets the maximum upload size (development default: `20` MiB). This is a configurable development value, not a performance or industry claim.
- `DOCUMENT_STORAGE_PATH` sets the local storage root (default: `./storage/documents`, resolved relative to the repository root). Files use generated UUID names; a sanitized original filename is retained as metadata.
- SHA-256 is calculated over the bytes copied into storage and recorded on the `DocumentVersion`.
- Each distinct file creates the next version number. Uploading identical bytes to the same document returns `409 Conflict` and removes the temporary stored copy. Identical content may be uploaded to a different document.

The storage service isolates filesystem operations behind `save`, `exists`, and `delete` methods so a later storage backend can be introduced without putting path operations in the API route. If the database write fails after a file is stored, the route removes that file. A process crash between those operations can still leave an orphan file; distributed transactions and reconciliation are outside this milestone.

Files are currently stored on the local filesystem and are not downloaded through a public endpoint. Upload does not parse, OCR, index, or otherwise process file contents. Asynchronous document processing is reserved for a future milestone.
