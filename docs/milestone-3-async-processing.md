# Milestone 3: Asynchronous Processing Foundation

Uploads now create a PostgreSQL `ProcessingJob` for the uploaded document version and enqueue a Celery task. The API returns after publishing the task; it does not wait for the worker to finish. `GET /api/v1/jobs/{job_id}` returns the job only when it belongs to the authenticated user's tenant.

## Component roles

- **FastAPI** validates the tenant-owned document upload, stores the version and job together, and publishes the job ID.
- **PostgreSQL** is authoritative for job state, attempts, progress, timestamps, and errors.
- **Redis** carries Celery messages; it is not used as the job database or result backend.
- **Celery** executes the `DOCUMENT_PROCESSING` task in the worker process.

## State and retry behavior

```text
PENDING → QUEUED → RUNNING → COMPLETED
                         ↘ FAILED
                         ↘ QUEUED → RUNNING  (bounded retry)
```

The upload transaction commits its `DocumentVersion` and queued job atomically. The API then publishes the job ID as Celery's task ID. A broker publish error marks the job `FAILED` with a generic `QUEUE_UNAVAILABLE` error. The worker retries only controlled retryable errors, with exponential delays capped at 8 seconds and at most two retries (three total attempts). Other errors become terminal `FAILED` jobs.

One `DOCUMENT_PROCESSING` job is allowed per document version by a database unique constraint. Repeated delivery of a completed or running task does not start another copy of the work. Job status queries are scoped by tenant.

## Current limitation

The worker only simulates work by advancing placeholder progress; it does not open, parse, OCR, or index the uploaded file. Without an outbox, a process crash after the database commit but before broker publication can leave a queued job without a Celery message. Broker publication failures are recorded, but automated recovery/reconciliation is not part of this milestone.
