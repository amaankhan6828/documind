"""Tenant-owned document version model."""

import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB

from backend.app.db.base import Base
from backend.app.models.mixins import TimestampMixin, UUIDPrimaryKey

JSON_VALUE = JSON().with_variant(JSONB(), "postgresql")


class DocumentVersion(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "tenant_id"],
            ["documents.id", "documents.tenant_id"],
            name="fk_document_versions_document_tenant",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "document_id", "version_number", name="uq_document_versions_number"
        ),
        UniqueConstraint(
            "document_id", "sha256", name="uq_document_versions_document_sha256"
        ),
        UniqueConstraint(
            "id", "document_id", "tenant_id", name="uq_document_versions_id_document_tenant"
        ),
        CheckConstraint("version_number > 0", name="version_number_positive"),
        Index("ix_document_versions_tenant_id", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    structure_metadata: Mapped[dict | None] = mapped_column(JSON_VALUE, nullable=True)

    tenant: Mapped["Tenant"] = relationship(overlaps="document,versions")
    document: Mapped["Document"] = relationship(
        back_populates="versions", overlaps="tenant"
    )
