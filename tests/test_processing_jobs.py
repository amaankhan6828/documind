from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.auth.tokens import create_access_token
from backend.app.core.config import get_settings
from backend.app.db.session import get_db
from backend.app.document_intelligence.schemas import (
    DocumentElementData,
    DocumentElementType,
    DocumentMetadata,
    DocumentPageData,
    DocumentSectionData,
    DocumentStructure,
    ProvenanceLocation,
)
from backend.app.main import app
from backend.app.models import (
    Document,
    DocumentChunk,
    DocumentChunkSource,
    DocumentElement,
    DocumentPage,
    DocumentVersion,
    InvalidJobTransition,
    ProcessingJob,
    ProcessingJobStatus,
    ProcessingJobType,
    Tenant,
    User,
)


def dummy_structure() -> DocumentStructure:
    provenance = [ProvenanceLocation(page_number=1, character_span=(0, 5))]
    return DocumentStructure(
        metadata=DocumentMetadata(
            source_filename="fixture.pdf",
            source_sha256="c" * 64,
            parser_version="test",
            page_count=1,
        ),
        pages=[DocumentPageData(page_number=1)],
        sections=[
            DocumentSectionData(
                ordinal=0,
                source_ref="#/texts/0",
                title="Title",
                level=1,
                provenance=provenance,
            )
        ],
        elements=[
            DocumentElementData(
                ordinal=0,
                source_ref="#/texts/0",
                element_type=DocumentElementType.TEXT,
                text_content="Title",
                section_source_ref="#/texts/0",
                provenance=provenance,
            )
        ],
    )


@pytest.fixture
def jobs_client(db_engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-jobs-secret-with-at-least-32-bytes")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    get_settings.cache_clear()

    def override_get_db() -> Iterator[Session]:
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


@pytest.fixture
def tenant_jobs(db_session: Session) -> dict[str, tuple[UUID, UUID, UUID]]:
    result = {}
    for suffix in ("a", "b"):
        tenant = Tenant(name=f"Tenant {suffix.upper()}", slug=f"job-tenant-{suffix}")
        user = User(
            tenant=tenant,
            email=f"job-user-{suffix}@example.com",
            display_name=f"Job User {suffix.upper()}",
        )
        db_session.add_all([tenant, user])
        db_session.flush()
        document = Document(tenant_id=tenant.id, title=f"Document {suffix.upper()}")
        db_session.add(document)
        db_session.flush()
        version = DocumentVersion(
            tenant_id=tenant.id,
            document_id=document.id,
            version_number=1,
        )
        db_session.add(version)
        db_session.flush()
        job = ProcessingJob(
            tenant_id=tenant.id,
            document_id=document.id,
            document_version_id=version.id,
            job_type=ProcessingJobType.DOCUMENT_PROCESSING.value,
            status=ProcessingJobStatus.QUEUED.value,
            attempt=0,
            progress=0,
        )
        db_session.add(job)
        db_session.flush()
        result[suffix] = (tenant.id, user.id, job.id)
    db_session.commit()
    return result


def headers(user_id: UUID, tenant_id: UUID) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token(user_id, tenant_id)}"
    }


def test_job_state_machine_and_tenant_ownership(
    db_session: Session,
    tenant_jobs: dict[str, tuple[UUID, UUID, UUID]],
) -> None:
    tenant_id, _, job_id = tenant_jobs["a"]
    job = db_session.get(ProcessingJob, job_id)
    assert job is not None and job.tenant_id == tenant_id
    assert job.status == ProcessingJobStatus.QUEUED.value

    job.transition(ProcessingJobStatus.RUNNING)
    assert job.started_at is not None
    job.transition(ProcessingJobStatus.COMPLETED)
    assert job.completed_at is not None
    with pytest.raises(InvalidJobTransition):
        job.transition(ProcessingJobStatus.QUEUED)


def test_database_allows_only_one_processing_job_per_document_version(
    db_session: Session,
    tenant_jobs: dict[str, tuple[UUID, UUID, UUID]],
) -> None:
    tenant_id, _, job_id = tenant_jobs["a"]
    original = db_session.get(ProcessingJob, job_id)
    assert original is not None
    duplicate = ProcessingJob(
        tenant_id=tenant_id,
        document_id=original.document_id,
        document_version_id=original.document_version_id,
        job_type=ProcessingJobType.DOCUMENT_PROCESSING.value,
        status=ProcessingJobStatus.QUEUED.value,
        attempt=0,
        progress=0,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_job_status_is_authenticated_and_tenant_scoped(
    jobs_client: TestClient,
    tenant_jobs: dict[str, tuple[UUID, UUID, UUID]],
) -> None:
    tenant_a, user_a, job_a = tenant_jobs["a"]
    tenant_b, user_b, job_b = tenant_jobs["b"]

    assert jobs_client.get(f"/api/v1/jobs/{job_a}").status_code == 401
    own_a = jobs_client.get(
        f"/api/v1/jobs/{job_a}", headers=headers(user_a, tenant_a)
    )
    hidden_b = jobs_client.get(
        f"/api/v1/jobs/{job_b}", headers=headers(user_a, tenant_a)
    )
    own_b = jobs_client.get(
        f"/api/v1/jobs/{job_b}", headers=headers(user_b, tenant_b)
    )
    hidden_a = jobs_client.get(
        f"/api/v1/jobs/{job_a}", headers=headers(user_b, tenant_b)
    )

    assert own_a.status_code == own_b.status_code == 200
    assert own_a.json()["job_id"] == str(job_a)
    assert own_a.json()["status"] == "QUEUED"
    assert set(own_a.json()) == {
        "job_id",
        "document_id",
        "document_version_id",
        "job_type",
        "status",
        "progress",
        "created_at",
        "started_at",
        "completed_at",
        "error",
    }
    assert hidden_a.status_code == hidden_b.status_code == 404
    assert hidden_a.json() == hidden_b.json()


def test_worker_completes_job_and_repeated_invocation_is_idempotent(
    db_engine,
    db_session: Session,
    tenant_jobs: dict[str, tuple[UUID, UUID, UUID]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.workers import tasks

    _, _, job_id = tenant_jobs["a"]
    monkeypatch.setattr(
        tasks,
        "get_session_factory",
        lambda: sessionmaker(bind=db_engine, expire_on_commit=False),
    )
    conversion_count = 0

    def convert(_version: DocumentVersion) -> DocumentStructure:
        nonlocal conversion_count
        conversion_count += 1
        return dummy_structure()

    monkeypatch.setattr(tasks, "ingest_document_version", convert)
    queued_index_versions: list[str] = []
    monkeypatch.setattr(
        tasks.index_document_version_task,
        "delay",
        lambda version_id: queued_index_versions.append(version_id),
    )
    attempted_lexical_versions: list[str] = []

    def lexical_broker_unavailable(version_id: str) -> None:
        attempted_lexical_versions.append(version_id)
        raise ConnectionError("test lexical broker outage")

    monkeypatch.setattr(
        tasks.index_lexical_document_version_task,
        "delay",
        lexical_broker_unavailable,
    )

    result = tasks.process_document.apply(args=[str(job_id)], throw=False)
    db_session.expire_all()
    job = db_session.get(ProcessingJob, job_id)
    assert result.result == "COMPLETED"
    assert job is not None
    assert job.status == "COMPLETED"
    assert job.attempt == 1
    assert job.progress == 100
    assert job.completed_at is not None
    assert conversion_count == 1
    element = db_session.scalar(
        select(DocumentElement).where(DocumentElement.document_version_id == job.document_version_id)
    )
    chunk = db_session.scalar(
        select(DocumentChunk).where(
            DocumentChunk.document_version_id == job.document_version_id
        )
    )
    chunk_source = db_session.scalar(
        select(DocumentChunkSource).where(
            DocumentChunkSource.document_version_id == job.document_version_id
        )
    )
    page = db_session.scalar(
        select(DocumentPage).where(DocumentPage.document_version_id == job.document_version_id)
    )
    assert element is not None and page is not None
    assert element.page_id == page.id
    assert element.source_provenance[0]["page_number"] == 1
    assert chunk is not None and chunk.text == "Title"
    assert chunk_source is not None
    assert chunk_source.document_element_id == element.id
    assert chunk_source.page_id == page.id
    assert queued_index_versions == [str(job.document_version_id)]
    assert attempted_lexical_versions == [str(job.document_version_id)]

    repeated = tasks.process_document.apply(args=[str(job_id)], throw=True)
    db_session.expire_all()
    assert repeated.result == "COMPLETED"
    assert db_session.get(ProcessingJob, job_id).attempt == 1
    assert conversion_count == 1
    assert db_session.scalar(
        select(func.count()).select_from(DocumentChunk).where(
            DocumentChunk.document_version_id == job.document_version_id
        )
    ) == 1


def test_qdrant_enqueue_failure_does_not_rollback_canonical_processing(
    db_engine,
    db_session: Session,
    tenant_jobs: dict[str, tuple[UUID, UUID, UUID]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.workers import tasks

    _, _, job_id = tenant_jobs["a"]
    monkeypatch.setattr(
        tasks,
        "get_session_factory",
        lambda: sessionmaker(bind=db_engine, expire_on_commit=False),
    )
    monkeypatch.setattr(tasks, "ingest_document_version", lambda _version: dummy_structure())

    def queue_unavailable(_version_id: str) -> None:
        raise ConnectionError("test broker outage")

    monkeypatch.setattr(tasks.index_document_version_task, "delay", queue_unavailable)
    monkeypatch.setattr(tasks.index_lexical_document_version_task, "delay", queue_unavailable)
    result = tasks.process_document.apply(args=[str(job_id)], throw=True)
    db_session.expire_all()
    job = db_session.get(ProcessingJob, job_id)

    assert result.result == "COMPLETED"
    assert job is not None and job.status == "COMPLETED" and job.progress == 100
    assert db_session.scalar(
        select(func.count()).select_from(DocumentChunk).where(
            DocumentChunk.document_version_id == job.document_version_id
        )
    ) == 1


def test_retryable_failures_are_bounded_and_end_in_failed_state(
    db_engine,
    db_session: Session,
    tenant_jobs: dict[str, tuple[UUID, UUID, UUID]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.workers import tasks

    _, _, job_id = tenant_jobs["a"]
    monkeypatch.setattr(
        tasks,
        "get_session_factory",
        lambda: sessionmaker(bind=db_engine, expire_on_commit=False),
    )
    failure_count = 0

    def fail_temporarily(_job_id: UUID) -> str:
        nonlocal failure_count
        failure_count += 1
        raise tasks.RetryableProcessingError("controlled failure")

    monkeypatch.setattr(tasks, "_process_structure", fail_temporarily)
    result = tasks.process_document.apply(args=[str(job_id)], throw=False)
    db_session.expire_all()
    job = db_session.get(ProcessingJob, job_id)

    assert result.result == "FAILED"
    assert failure_count == tasks.MAX_PROCESSING_RETRIES + 1
    assert job is not None
    assert job.status == "FAILED"
    assert job.attempt == tasks.MAX_PROCESSING_RETRIES + 1
    assert job.error_code == "PROCESSING_RETRIES_EXHAUSTED"
    assert job.completed_at is not None


def test_malformed_document_fails_with_safe_error(
    db_engine,
    db_session: Session,
    tenant_jobs: dict[str, tuple[UUID, UUID, UUID]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.document_intelligence.docling_adapter import DocumentIngestionError
    from backend.app.workers import tasks

    _, _, job_id = tenant_jobs["a"]
    monkeypatch.setattr(
        tasks,
        "get_session_factory",
        lambda: sessionmaker(bind=db_engine, expire_on_commit=False),
    )

    def fail_conversion(_job_id: UUID) -> str:
        raise DocumentIngestionError("sensitive parser details")

    monkeypatch.setattr(tasks, "_process_structure", fail_conversion)
    result = tasks.process_document.apply(args=[str(job_id)], throw=False)
    db_session.expire_all()
    job = db_session.get(ProcessingJob, job_id)

    assert result.result == "FAILED"
    assert job is not None and job.status == "FAILED"
    assert job.error_code == "DOCUMENT_INGESTION_FAILED"
    assert job.error_message == "The uploaded document could not be processed"
    assert "sensitive" not in job.error_message
