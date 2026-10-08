"""Small Celery queue adapter used by the upload API."""

from uuid import UUID

from backend.app.workers.tasks import process_document


def enqueue_document_processing(job_id: UUID) -> None:
    """Publish a job using its database ID as the stable Celery task ID."""
    process_document.apply_async(args=[str(job_id)], task_id=str(job_id))
