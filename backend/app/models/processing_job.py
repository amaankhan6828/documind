"""Persistent state for asynchronous document processing jobs."""

from datetime import UTC, datetime
from enum import StrEnum
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.models.mixins import TimestampMixin, UUIDPrimaryKey


class ProcessingJobType(StrEnum):
    DOCUMENT_PROCESSING = "DOCUMENT_PROCESSING"


class ProcessingJobStatus(StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class InvalidJobTransition(ValueError):
    """Raised when a processing job is moved along an invalid state transition."""


_ALLOWED_TRANSITIONS = {
    ProcessingJobStatus.PENDING: {ProcessingJobStatus.QUEUED},
    ProcessingJobStatus.QUEUED: {
        ProcessingJobStatus.RUNNING,
        ProcessingJobStatus.FAILED,
    },
    ProcessingJobStatus.RUNNING: {
        ProcessingJobStatus.QUEUED,
        ProcessingJobStatus.COMPLETED,
        ProcessingJobStatus.FAILED,
    },
    ProcessingJobStatus.COMPLETED: set(),
    ProcessingJobStatus.FAILED: set(),
}


class ProcessingJob(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "tenant_id"],
            ["documents.id", "documents.tenant_id"],
            name="fk_processing_jobs_document_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_version_id", "document_id", "tenant_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.tenant_id",
            ],
            name="fk_processing_jobs_version_document_tenant",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "document_version_id", "job_type", name="uq_processing_jobs_version_type"
        ),
        CheckConstraint(
            "job_type = 'DOCUMENT_PROCESSING'", name="processing_job_type_valid"
        ),
        CheckConstraint(
            "status IN ('PENDING', 'QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')",
            name="processing_job_status_valid",
        ),
        CheckConstraint("attempt >= 0", name="processing_job_attempt_nonnegative"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="processing_job_progress_range"),
        Index("ix_processing_jobs_tenant_status", "tenant_id", "status"),
        Index("ix_processing_jobs_document_id", "document_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    document_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    job_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default=ProcessingJobType.DOCUMENT_PROCESSING.value
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ProcessingJobStatus.PENDING.value
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    def transition(self, new_status: ProcessingJobStatus) -> None:
        current = ProcessingJobStatus(self.status)
        if new_status not in _ALLOWED_TRANSITIONS[current]:
            raise InvalidJobTransition(f"Cannot transition job from {current} to {new_status}")
        self.status = new_status.value
        if new_status is ProcessingJobStatus.RUNNING:
            self.started_at = datetime.now(UTC)
        if new_status in {ProcessingJobStatus.COMPLETED, ProcessingJobStatus.FAILED}:
            self.completed_at = datetime.now(UTC)
