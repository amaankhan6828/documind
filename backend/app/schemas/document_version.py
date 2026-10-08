"""Document version upload response schemas."""

from uuid import UUID

from pydantic import BaseModel


class DocumentVersionUploadResponse(BaseModel):
    document_id: UUID
    document_version_id: UUID
    version_number: int
    original_filename: str
    mime_type: str
    file_size: int
    sha256: str
    status: str = "STORED"
    job_id: UUID
    job_status: str
