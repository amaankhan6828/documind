"""Safe processing job status responses."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ProcessingJobErrorResponse(BaseModel):
    code: str
    message: str


class ProcessingJobResponse(BaseModel):
    job_id: UUID
    document_id: UUID
    document_version_id: UUID
    job_type: str
    status: str
    progress: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error: ProcessingJobErrorResponse | None
