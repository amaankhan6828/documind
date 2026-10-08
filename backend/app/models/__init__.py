"""Domain models registered on the shared SQLAlchemy metadata."""

from backend.app.models.document import Document
from backend.app.models.document_chunk import DocumentChunk, DocumentChunkSource
from backend.app.models.document_structure import (
    DocumentElement,
    DocumentPage,
    DocumentSection,
)
from backend.app.models.document_version import DocumentVersion
from backend.app.models.processing_job import (
    InvalidJobTransition,
    ProcessingJob,
    ProcessingJobStatus,
    ProcessingJobType,
)
from backend.app.models.tenant import Tenant
from backend.app.models.user import User

__all__ = [
    "Tenant",
    "User",
    "Document",
    "DocumentChunk",
    "DocumentChunkSource",
    "DocumentVersion",
    "DocumentPage",
    "DocumentSection",
    "DocumentElement",
    "ProcessingJob",
    "ProcessingJobStatus",
    "ProcessingJobType",
    "InvalidJobTransition",
]
