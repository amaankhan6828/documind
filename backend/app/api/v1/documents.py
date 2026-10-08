"""Tenant-scoped logical document endpoints."""

from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.auth.dependencies import get_current_user
from backend.app.db.session import get_db
from backend.app.core.config import REPOSITORY_ROOT, get_settings
from backend.app.models import (
    Document,
    DocumentVersion,
    ProcessingJob,
    ProcessingJobStatus,
    ProcessingJobType,
    User,
)
from backend.app.schemas.document import DocumentCreate, DocumentResponse, DocumentUpdate
from backend.app.schemas.document_version import DocumentVersionUploadResponse
from backend.app.storage.local import (
    InvalidUploadedFileError,
    LocalStorage,
    UploadTooLargeError,
)
from backend.app.storage.validation import (
    UnsupportedUploadTypeError,
    validate_upload_metadata,
)
from backend.app.workers.queue import enqueue_document_processing

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])
DatabaseSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


def _get_tenant_document(
    db: Session, document_id: UUID, tenant_id: UUID
) -> Document:
    """Fetch a document only within the caller's tenant."""
    document = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == tenant_id,
        )
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    return document


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_document(
    payload: DocumentCreate,
    db: DatabaseSession,
    current_user: CurrentUser,
) -> Document:
    document = Document(
        tenant_id=current_user.tenant_id,
        title=payload.title,
        document_type=payload.document_type,
        issuer=payload.issuer,
        description=payload.description,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


@router.get("", response_model=list[DocumentResponse])
def list_documents(
    db: DatabaseSession,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Document]:
    statement = (
        select(Document)
        .where(Document.tenant_id == current_user.tenant_id)
        .order_by(Document.created_at.desc(), Document.id)
        .offset(offset)
        .limit(limit)
    )
    return list(db.scalars(statement).all())


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: UUID,
    db: DatabaseSession,
    current_user: CurrentUser,
) -> Document:
    return _get_tenant_document(db, document_id, current_user.tenant_id)


@router.patch("/{document_id}", response_model=DocumentResponse)
def update_document(
    document_id: UUID,
    payload: DocumentUpdate,
    db: DatabaseSession,
    current_user: CurrentUser,
) -> Document:
    document = _get_tenant_document(db, document_id, current_user.tenant_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(document, field, value)
    db.commit()
    db.refresh(document)
    return document


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: UUID,
    db: DatabaseSession,
    current_user: CurrentUser,
) -> Response:
    document = _get_tenant_document(db, document_id, current_user.tenant_id)
    db.delete(document)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{document_id}/versions",
    response_model=DocumentVersionUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_document_version(
    document_id: UUID,
    db: DatabaseSession,
    current_user: CurrentUser,
    file: UploadFile = File(...),
) -> DocumentVersionUploadResponse:
    document = db.scalar(
        select(Document)
        .where(
            Document.id == document_id,
            Document.tenant_id == current_user.tenant_id,
        )
        .with_for_update()
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    try:
        original_filename, extension, mime_type = validate_upload_metadata(
            file.filename, file.content_type
        )
    except UnsupportedUploadTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF and DOCX files with matching media types are supported",
        ) from exc

    settings = get_settings()
    storage_path = settings.document_storage_path
    if not storage_path.is_absolute():
        storage_path = REPOSITORY_ROOT / storage_path
    storage = LocalStorage(storage_path)
    try:
        stored_file = storage.save(
            file.file,
            extension,
            settings.max_upload_size_mb * 1024 * 1024,
        )
    except UploadTooLargeError as exc:
        raise HTTPException(
            status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            detail="Uploaded file exceeds the configured size limit",
        ) from exc
    except InvalidUploadedFileError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Uploaded file content does not match the declared file type",
        ) from exc

    try:
        duplicate = db.scalar(
            select(DocumentVersion.id).where(
                DocumentVersion.document_id == document.id,
                DocumentVersion.tenant_id == current_user.tenant_id,
                DocumentVersion.sha256 == stored_file.sha256,
            )
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This file has already been uploaded to this document",
            )

        current_version = db.scalar(
            select(func.max(DocumentVersion.version_number)).where(
                DocumentVersion.document_id == document.id,
                DocumentVersion.tenant_id == current_user.tenant_id,
            )
        )
        version = DocumentVersion(
            document_id=document.id,
            tenant_id=current_user.tenant_id,
            version_number=(current_version or 0) + 1,
            original_filename=original_filename,
            storage_key=stored_file.storage_key,
            mime_type=mime_type,
            file_size=stored_file.file_size,
            sha256=stored_file.sha256,
        )
        db.add(version)
        db.flush()
        job = ProcessingJob(
            tenant_id=current_user.tenant_id,
            document_id=document.id,
            document_version_id=version.id,
            job_type=ProcessingJobType.DOCUMENT_PROCESSING.value,
            status=ProcessingJobStatus.PENDING.value,
            attempt=0,
            progress=0,
        )
        job.transition(ProcessingJobStatus.QUEUED)
        db.add(job)
        db.commit()
    except HTTPException:
        db.rollback()
        storage.delete(stored_file.storage_key)
        raise
    except IntegrityError as exc:
        db.rollback()
        storage.delete(stored_file.storage_key)
        duplicate = db.scalar(
            select(DocumentVersion.id).where(
                DocumentVersion.document_id == document.id,
                DocumentVersion.tenant_id == current_user.tenant_id,
                DocumentVersion.sha256 == stored_file.sha256,
            )
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This file has already been uploaded to this document",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A concurrent document version update occurred; retry the upload",
        ) from exc
    except Exception as exc:
        db.rollback()
        storage.delete(stored_file.storage_key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to save document version",
        ) from exc

    try:
        enqueue_document_processing(job.id)
    except Exception:
        db.rollback()
        job.transition(ProcessingJobStatus.FAILED)
        job.error_code = "QUEUE_UNAVAILABLE"
        job.error_message = "Processing task could not be queued"
        db.commit()

    return DocumentVersionUploadResponse(
        document_id=document.id,
        document_version_id=version.id,
        version_number=version.version_number,
        original_filename=version.original_filename,
        mime_type=version.mime_type,
        file_size=version.file_size,
        sha256=version.sha256,
        job_id=job.id,
        job_status=job.status,
    )
