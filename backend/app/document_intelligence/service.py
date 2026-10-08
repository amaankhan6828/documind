"""Safe input resolution and provider-neutral ingestion orchestration."""

import hashlib
from pathlib import Path

from backend.app.core.config import REPOSITORY_ROOT, get_settings
from backend.app.document_intelligence.docling_adapter import (
    DoclingAdapter,
    DocumentIngestionError,
)
from backend.app.document_intelligence.schemas import DocumentStructure
from backend.app.models import DocumentVersion


EXPECTED_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def stored_file_path(version: DocumentVersion) -> Path:
    """Resolve a version's generated key and verify it remains in storage root."""
    settings = get_settings()
    root = settings.document_storage_path
    if not root.is_absolute():
        root = REPOSITORY_ROOT / root
    root = root.expanduser().resolve()

    storage_key = version.storage_key
    if not storage_key or Path(storage_key).name != storage_key:
        raise DocumentIngestionError("Stored file reference is invalid")
    try:
        path = (root / storage_key).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise DocumentIngestionError("Stored file is unavailable") from exc
    if path.parent != root or not path.is_file():
        raise DocumentIngestionError("Stored file is unavailable")

    expected_mime = EXPECTED_MIME_TYPES.get(path.suffix.lower())
    if expected_mime is None or version.mime_type != expected_mime:
        raise DocumentIngestionError("Stored file type is unsupported")
    if version.file_size is not None and path.stat().st_size != version.file_size:
        raise DocumentIngestionError("Stored file size does not match its record")
    if path.stat().st_size > settings.max_upload_size_mb * 1024 * 1024:
        raise DocumentIngestionError("Stored file exceeds the configured size limit")

    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    if version.sha256 and digest.hexdigest() != version.sha256:
        raise DocumentIngestionError("Stored file checksum does not match its record")
    return path


def ingest_document_version(version: DocumentVersion) -> DocumentStructure:
    """Validate the stored file and convert it with the isolated Docling adapter."""
    path = stored_file_path(version)
    if not version.original_filename or not version.sha256:
        raise DocumentIngestionError("Document version metadata is incomplete")
    return DoclingAdapter().convert(
        path,
        source_filename=version.original_filename,
        source_sha256=version.sha256,
    )
