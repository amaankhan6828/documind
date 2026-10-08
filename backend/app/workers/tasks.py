"""Background tasks for asynchronous document processing."""

import logging
from uuid import UUID

from celery import Task
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.chunking.persistence import replace_document_chunks
from backend.app.chunking.service import build_chunks
from backend.app.core.config import get_settings
from backend.app.document_intelligence.docling_adapter import DocumentIngestionError
from backend.app.document_intelligence.persistence import replace_document_structure
from backend.app.document_intelligence.service import ingest_document_version
from backend.app.db.session import get_session_factory
from backend.app.models import (
    DocumentVersion,
    DocumentElement,
    DocumentPage,
    ProcessingJob,
    ProcessingJobStatus,
)
from backend.app.retrieval.tasks import index_document_version_task
from backend.app.workers.celery_app import celery_app

MAX_PROCESSING_RETRIES = 2
logger = logging.getLogger(__name__)


class RetryableProcessingError(Exception):
    """A temporary error that may succeed on a bounded retry."""


def _locked_job(session: Session, job_id: UUID) -> ProcessingJob | None:
    return session.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.id == job_id)
        .with_for_update()
    )


def _claim_job(job_id: UUID) -> str | None:
    with get_session_factory()() as session:
        job = _locked_job(session, job_id)
        if job is None:
            return "MISSING"
        state = ProcessingJobStatus(job.status)
        if state in {
            ProcessingJobStatus.RUNNING,
            ProcessingJobStatus.COMPLETED,
            ProcessingJobStatus.FAILED,
        }:
            return state.value
        job.transition(ProcessingJobStatus.RUNNING)
        job.attempt += 1
        job.progress = 0
        job.error_code = None
        job.error_message = None
        session.commit()
        return None


def _set_progress(job_id: UUID, progress: int) -> None:
    with get_session_factory()() as session:
        job = _locked_job(session, job_id)
        if job is not None and job.status == ProcessingJobStatus.RUNNING.value:
            job.progress = progress
            session.commit()


def _process_structure(job_id: UUID) -> str:
    """Convert a stored version, then atomically persist structure and completion."""
    with get_session_factory()() as session:
        job = _locked_job(session, job_id)
        if job is None:
            return "MISSING"
        version = session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.id == job.document_version_id,
                DocumentVersion.document_id == job.document_id,
                DocumentVersion.tenant_id == job.tenant_id,
            )
        )
        if version is None:
            raise DocumentIngestionError("Document version is unavailable")

    _set_progress(job_id, 10)
    structure = ingest_document_version(version)
    _set_progress(job_id, 75)

    with get_session_factory()() as session:
        job = _locked_job(session, job_id)
        if job is None or job.status != ProcessingJobStatus.RUNNING.value:
            return "MISSING"
        version = session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.id == job.document_version_id,
                DocumentVersion.document_id == job.document_id,
                DocumentVersion.tenant_id == job.tenant_id,
            )
        )
        if version is None:
            raise DocumentIngestionError("Document version is unavailable")
        replace_document_structure(session, version, structure)
        pages = list(
            session.scalars(
                select(DocumentPage).where(
                    DocumentPage.document_version_id == version.id
                )
            ).all()
        )
        elements = list(
            session.scalars(
                select(DocumentElement)
                .where(DocumentElement.document_version_id == version.id)
                .order_by(DocumentElement.ordinal)
            ).all()
        )
        settings = get_settings()
        chunks = build_chunks(
            version,
            elements,
            pages,
            max_characters=settings.chunk_max_characters,
            overlap_characters=settings.chunk_overlap_characters,
        )
        replace_document_chunks(session, version, chunks)
        job.progress = 100
        job.transition(ProcessingJobStatus.COMPLETED)
        indexed_version_id = version.id
        session.commit()
    # Qdrant is derived state. Dispatch only after PostgreSQL commits, and do not
    # change canonical job success if the broker/index is temporarily unavailable.
    try:
        index_document_version_task.delay(str(indexed_version_id))
    except Exception:
        logger.warning(
            "Could not enqueue dense indexing for document version %s; "
            "canonical chunks remain committed and can be rebuilt",
            indexed_version_id,
            exc_info=True,
        )
    return ProcessingJobStatus.COMPLETED.value


def _mark_failed(job_id: UUID, code: str, message: str) -> None:
    with get_session_factory()() as session:
        job = _locked_job(session, job_id)
        if job is not None and job.status == ProcessingJobStatus.RUNNING.value:
            job.transition(ProcessingJobStatus.FAILED)
            job.error_code = code
            job.error_message = message[:1000]
            session.commit()


class DocumentProcessingTask(Task):
    max_retries = MAX_PROCESSING_RETRIES
    default_retry_delay = 1


@celery_app.task(
    bind=True,
    base=DocumentProcessingTask,
    name="documind.process_document",
    ignore_result=True,
)
def process_document(self: DocumentProcessingTask, job_id: str) -> str:
    try:
        parsed_job_id = UUID(job_id)
    except ValueError:
        return "INVALID_JOB_ID"

    initial_state = _claim_job(parsed_job_id)
    if initial_state is not None:
        return initial_state

    try:
        return _process_structure(parsed_job_id)
    except RetryableProcessingError as exc:
        with get_session_factory()() as session:
            job = _locked_job(session, parsed_job_id)
            if job is None or job.status != ProcessingJobStatus.RUNNING.value:
                return "MISSING"
            if self.request.retries < self.max_retries:
                job.transition(ProcessingJobStatus.QUEUED)
                job.progress = 0
                job.error_code = "TEMPORARY_PROCESSING_ERROR"
                job.error_message = "Temporary processing error; retry scheduled"
                session.commit()
                countdown = min(2 ** self.request.retries, 8)
                raise self.retry(exc=exc, countdown=countdown)
            job.transition(ProcessingJobStatus.FAILED)
            job.error_code = "PROCESSING_RETRIES_EXHAUSTED"
            job.error_message = "Processing failed after the retry limit"
            session.commit()
            return ProcessingJobStatus.FAILED.value
    except DocumentIngestionError:
        _mark_failed(
            parsed_job_id,
            "DOCUMENT_INGESTION_FAILED",
            "The uploaded document could not be processed",
        )
        return ProcessingJobStatus.FAILED.value
    except Exception:
        _mark_failed(
            parsed_job_id,
            "PROCESSING_FAILED",
            "Document processing failed",
        )
        return ProcessingJobStatus.FAILED.value

    return ProcessingJobStatus.COMPLETED.value
