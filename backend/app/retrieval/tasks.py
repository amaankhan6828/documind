"""Independent retryable Celery task for the rebuildable Qdrant projection."""

from uuid import UUID

from celery import Task

from backend.app.retrieval.indexing import index_document_version
from backend.app.retrieval.qdrant import QdrantIndexError
from backend.app.workers.celery_app import celery_app


class DenseIndexTask(Task):
    max_retries = 3


@celery_app.task(
    bind=True,
    base=DenseIndexTask,
    name="documind.index_document_version",
    ignore_result=True,
)
def index_document_version_task(self: DenseIndexTask, document_version_id: str) -> int:
    """Retry Qdrant transport/write failures without changing canonical job state."""
    try:
        return index_document_version(UUID(document_version_id))
    except QdrantIndexError as exc:
        if self.request.retries >= self.max_retries:
            raise
        countdown = min(2 ** self.request.retries, 30)
        raise self.retry(exc=exc, countdown=countdown)
