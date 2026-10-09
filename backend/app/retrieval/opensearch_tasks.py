"""Independent retryable Celery task for the OpenSearch lexical projection."""

from uuid import UUID

from celery import Task

from backend.app.retrieval.lexical_indexing import index_lexical_document_version
from backend.app.retrieval.opensearch import OpenSearchIndexError
from backend.app.workers.celery_app import celery_app


class LexicalIndexTask(Task):
    max_retries = 3


@celery_app.task(
    bind=True,
    base=LexicalIndexTask,
    name="documind.index_lexical_document_version",
    ignore_result=True,
)
def index_lexical_document_version_task(
    self: LexicalIndexTask, document_version_id: str
) -> int:
    """Retry OpenSearch transport/write failures without changing canonical job state."""
    try:
        return index_lexical_document_version(UUID(document_version_id))
    except OpenSearchIndexError as exc:
        if self.request.retries >= self.max_retries:
            raise
        countdown = min(2 ** self.request.retries, 30)
        raise self.retry(exc=exc, countdown=countdown)
