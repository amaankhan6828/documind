"""Tenant-scoped processing job status endpoint."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.auth.dependencies import get_current_user
from backend.app.db.session import get_db
from backend.app.models import ProcessingJob, User
from backend.app.schemas.processing_job import (
    ProcessingJobErrorResponse,
    ProcessingJobResponse,
)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])
DatabaseSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("/{job_id}", response_model=ProcessingJobResponse)
def get_processing_job(
    job_id: UUID,
    db: DatabaseSession,
    current_user: CurrentUser,
) -> ProcessingJobResponse:
    job = db.scalar(
        select(ProcessingJob).where(
            ProcessingJob.id == job_id,
            ProcessingJob.tenant_id == current_user.tenant_id,
        )
    )
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Processing job not found",
        )

    error = None
    if job.error_code:
        error = ProcessingJobErrorResponse(
            code=job.error_code,
            message=job.error_message or "Processing failed",
        )
    return ProcessingJobResponse(
        job_id=job.id,
        document_id=job.document_id,
        document_version_id=job.document_version_id,
        job_type=job.job_type,
        status=job.status,
        progress=job.progress,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        error=error,
    )
