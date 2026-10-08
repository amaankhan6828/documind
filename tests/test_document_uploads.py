from collections.abc import Iterator
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.auth.tokens import create_access_token
from backend.app.core.config import get_settings
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.models import Document, DocumentVersion, ProcessingJob, Tenant, User

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

def make_pdf() -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] /Contents 4 0 R >>",
        b"<< /Length 0 >>\nstream\n\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode())
        output.extend(body + b"\nendobj\n")
    xref_offset = len(output)
    output.extend(b"xref\n0 5\n0000000000 65535 f \n")
    for offset in offsets:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(output)


PDF_BYTES = make_pdf()


@pytest.fixture
def queued_job_ids() -> list[UUID]:
    return []


@pytest.fixture
def upload_client(
    db_engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    queued_job_ids: list[UUID],
) -> Iterator[TestClient]:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-upload-secret-with-at-least-32-bytes")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    monkeypatch.setenv("DOCUMENT_STORAGE_PATH", str(tmp_path / "uploads"))
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "20")
    get_settings.cache_clear()
    from backend.app.api.v1 import documents as document_routes

    monkeypatch.setattr(
        document_routes,
        "enqueue_document_processing",
        lambda job_id: queued_job_ids.append(job_id),
    )

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


def token_headers(user_id: UUID, tenant_id: UUID) -> dict[str, str]:
    token = create_access_token(user_id, tenant_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def tenant_users(db_session: Session) -> dict[str, tuple[UUID, UUID]]:
    result = {}
    for suffix in ("a", "b"):
        tenant = Tenant(name=f"Tenant {suffix.upper()}", slug=f"tenant-{suffix}")
        user = User(
            tenant=tenant,
            email=f"user-{suffix}@example.com",
            display_name=f"User {suffix.upper()}",
        )
        db_session.add_all([tenant, user])
        db_session.flush()
        result[suffix] = (tenant.id, user.id)
    db_session.commit()
    return result


def upload(
    client: TestClient,
    document_id: UUID,
    filename: str,
    content: bytes,
    content_type: str,
    headers: dict[str, str],
):
    return client.post(
        f"/api/v1/documents/{document_id}/versions",
        headers=headers,
        files={"file": (filename, content, content_type)},
    )


def make_docx() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    return buffer.getvalue()


def test_authenticated_pdf_upload_persists_file_hash_and_version(
    upload_client: TestClient,
    tenant_users: dict[str, tuple[UUID, UUID]],
    db_session: Session,
    tmp_path: Path,
    queued_job_ids: list[UUID],
) -> None:
    tenant_id, user_id = tenant_users["a"]
    document = Document(tenant_id=tenant_id, title="Annual Report")
    db_session.add(document)
    db_session.commit()
    headers = token_headers(user_id, tenant_id)

    response = upload(
        upload_client, document.id, "annual.pdf", PDF_BYTES, PDF_MIME, headers
    )

    assert response.status_code == 201, response.text
    result = response.json()
    assert result["document_id"] == str(document.id)
    assert result["version_number"] == 1
    assert result["original_filename"] == "annual.pdf"
    assert result["mime_type"] == PDF_MIME
    assert result["file_size"] == len(PDF_BYTES)
    assert result["sha256"] == sha256(PDF_BYTES).hexdigest()
    assert result["status"] == "STORED"
    assert result["job_status"] == "QUEUED"
    assert len(queued_job_ids) == 1
    storage_root = tmp_path / "uploads"
    version = db_session.get(DocumentVersion, UUID(result["document_version_id"]))
    assert version is not None
    assert version.tenant_id == tenant_id
    assert version.document_id == document.id
    assert version.sha256 == sha256(PDF_BYTES).hexdigest()
    job = db_session.get(ProcessingJob, UUID(result["job_id"]))
    assert job is not None
    assert job.document_version_id == version.id
    assert job.document_id == document.id
    assert job.tenant_id == tenant_id
    assert job.id in queued_job_ids
    assert version.storage_key != "annual.pdf"
    stored_path = storage_root / version.storage_key
    assert stored_path.resolve().parent == storage_root.resolve()
    assert stored_path.read_bytes() == PDF_BYTES


def test_docx_upload_and_next_version_number(
    upload_client: TestClient,
    tenant_users: dict[str, tuple[UUID, UUID]],
    db_session: Session,
) -> None:
    tenant_id, user_id = tenant_users["a"]
    document = Document(tenant_id=tenant_id, title="Agreement")
    db_session.add(document)
    db_session.commit()
    headers = token_headers(user_id, tenant_id)
    first = upload(upload_client, document.id, "agreement.docx", make_docx(), DOCX_MIME, headers)
    second_content = PDF_BYTES + b"version 2"
    second = upload(upload_client, document.id, "agreement.pdf", second_content, PDF_MIME, headers)

    assert first.status_code == second.status_code == 201
    assert first.json()["version_number"] == 1
    assert second.json()["version_number"] == 2
    versions = db_session.scalars(
        select(DocumentVersion).where(DocumentVersion.document_id == document.id)
    ).all()
    assert {version.version_number for version in versions} == {1, 2}


@pytest.mark.parametrize(
    "filename,content,content_type",
    [
        ("notes.txt", b"text", "text/plain"),
        ("file.pdf", PDF_BYTES, "application/octet-stream"),
        ("file.pdf", b"not a PDF", PDF_MIME),
        ("file.pdf", b"not a header %PDF-1.7", PDF_MIME),
        ("file.docx", b"not a zip archive", DOCX_MIME),
        ("file.docx", make_docx(), PDF_MIME),
    ],
)
def test_unsupported_or_mismatched_uploads_are_rejected(
    upload_client: TestClient,
    tenant_users: dict[str, tuple[UUID, UUID]],
    db_session: Session,
    filename: str,
    content: bytes,
    content_type: str,
) -> None:
    tenant_id, user_id = tenant_users["a"]
    document = Document(tenant_id=tenant_id, title="Report")
    db_session.add(document)
    db_session.commit()

    response = upload(
        upload_client,
        document.id,
        filename,
        content,
        content_type,
        token_headers(user_id, tenant_id),
    )

    assert response.status_code == 415
    assert db_session.scalars(
        select(DocumentVersion).where(DocumentVersion.document_id == document.id)
    ).all() == []


def test_upload_size_limit_is_configurable(
    upload_client: TestClient,
    tenant_users: dict[str, tuple[UUID, UUID]],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, user_id = tenant_users["a"]
    document = Document(tenant_id=tenant_id, title="Report")
    db_session.add(document)
    db_session.commit()
    headers = token_headers(user_id, tenant_id)

    accepted = upload(upload_client, document.id, "small.pdf", PDF_BYTES, PDF_MIME, headers)
    assert accepted.status_code == 201

    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "1")
    get_settings.cache_clear()
    rejected = upload(
        upload_client,
        document.id,
        "large.pdf",
        b"%PDF-1.7\n" + b"x" * (1024 * 1024),
        PDF_MIME,
        headers,
    )
    assert rejected.status_code == 413, rejected.text


@pytest.mark.parametrize(
    "filename",
    ["../../evil.pdf", "../../../tmp/test.pdf", "folder/file.pdf"],
)
def test_client_path_components_never_control_storage_path(
    upload_client: TestClient,
    tenant_users: dict[str, tuple[UUID, UUID]],
    db_session: Session,
    tmp_path: Path,
    filename: str,
) -> None:
    tenant_id, user_id = tenant_users["a"]
    document = Document(tenant_id=tenant_id, title="Report")
    db_session.add(document)
    db_session.commit()

    response = upload(
        upload_client,
        document.id,
        filename,
        PDF_BYTES,
        PDF_MIME,
        token_headers(user_id, tenant_id),
    )

    assert response.status_code == 201
    result = response.json()
    assert "/" not in result["original_filename"]
    assert result["original_filename"].endswith(".pdf")
    actual_stored_path = tmp_path / "uploads" / db_session.get(
        DocumentVersion, UUID(result["document_version_id"])
    ).storage_key
    assert actual_stored_path.exists()
    assert actual_stored_path.resolve().parent == (tmp_path / "uploads").resolve()
    assert actual_stored_path.name != result["original_filename"]


def test_same_hash_same_document_returns_conflict_without_new_version(
    upload_client: TestClient,
    tenant_users: dict[str, tuple[UUID, UUID]],
    db_session: Session,
    tmp_path: Path,
    queued_job_ids: list[UUID],
) -> None:
    tenant_id, user_id = tenant_users["a"]
    document = Document(tenant_id=tenant_id, title="Report")
    db_session.add(document)
    db_session.commit()
    headers = token_headers(user_id, tenant_id)

    first = upload(upload_client, document.id, "report.pdf", PDF_BYTES, PDF_MIME, headers)
    duplicate = upload(upload_client, document.id, "renamed.pdf", PDF_BYTES, PDF_MIME, headers)

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert db_session.query(DocumentVersion).filter_by(document_id=document.id).count() == 1
    assert len(queued_job_ids) == 1
    files = list((tmp_path / "uploads").iterdir())
    assert len(files) == 1


def test_queue_publish_failure_is_persisted_on_the_job(
    upload_client: TestClient,
    tenant_users: dict[str, tuple[UUID, UUID]],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.api.v1 import documents as document_routes

    tenant_id, user_id = tenant_users["a"]
    document = Document(tenant_id=tenant_id, title="Queue failure")
    db_session.add(document)
    db_session.commit()

    def reject_publish(_job_id: UUID) -> None:
        raise RuntimeError("controlled broker failure")

    monkeypatch.setattr(document_routes, "enqueue_document_processing", reject_publish)
    response = upload(
        upload_client,
        document.id,
        "queue-failure.pdf",
        PDF_BYTES,
        PDF_MIME,
        token_headers(user_id, tenant_id),
    )

    assert response.status_code == 201
    result = response.json()
    assert result["job_status"] == "FAILED"
    job = db_session.get(ProcessingJob, UUID(result["job_id"]))
    assert job is not None
    assert job.status == "FAILED"
    assert job.error_code == "QUEUE_UNAVAILABLE"


def test_tenant_users_can_upload_only_to_their_own_documents(
    upload_client: TestClient,
    tenant_users: dict[str, tuple[UUID, UUID]],
    db_session: Session,
    tmp_path: Path,
) -> None:
    tenant_a, user_a = tenant_users["a"]
    tenant_b, user_b = tenant_users["b"]
    doc_a = Document(tenant_id=tenant_a, title="A")
    doc_b = Document(tenant_id=tenant_b, title="B")
    db_session.add_all([doc_a, doc_b])
    db_session.commit()

    for user_id, tenant_id, own, other in (
        (user_a, tenant_a, doc_a, doc_b),
        (user_b, tenant_b, doc_b, doc_a),
    ):
        headers = token_headers(user_id, tenant_id)
        own_upload = upload(upload_client, own.id, "own.pdf", PDF_BYTES, PDF_MIME, headers)
        before = set((tmp_path / "uploads").iterdir()) if (tmp_path / "uploads").exists() else set()
        cross_upload = upload(
            upload_client, other.id, "other.pdf", PDF_BYTES + b"unique", PDF_MIME, headers
        )
        assert own_upload.status_code == 201
        assert cross_upload.status_code == 404
        assert set((tmp_path / "uploads").iterdir()) == before


def test_upload_requires_authentication(
    upload_client: TestClient,
    tenant_users: dict[str, tuple[UUID, UUID]],
    db_session: Session,
) -> None:
    tenant_id, _ = tenant_users["a"]
    document = Document(tenant_id=tenant_id, title="Report")
    db_session.add(document)
    db_session.commit()
    response = upload(upload_client, document.id, "report.pdf", PDF_BYTES, PDF_MIME, {})
    assert response.status_code == 401


def test_database_failure_removes_the_newly_stored_file(
    upload_client: TestClient,
    tenant_users: dict[str, tuple[UUID, UUID]],
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, user_id = tenant_users["a"]
    document = Document(tenant_id=tenant_id, title="Report")
    db_session.add(document)
    db_session.commit()

    def fail_commit(self) -> None:
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(Session, "commit", fail_commit)
    response = upload(
        upload_client,
        document.id,
        "report.pdf",
        PDF_BYTES,
        PDF_MIME,
        token_headers(user_id, tenant_id),
    )

    assert response.status_code == 500
    assert list((tmp_path / "uploads").iterdir()) == []
