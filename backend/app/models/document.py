"""Tenant-owned document model."""

import uuid

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.models.mixins import TimestampMixin, UUIDPrimaryKey


class Document(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        # Required as the target of DocumentVersion's tenant-matching composite FK.
        UniqueConstraint("id", "tenant_id", name="uq_documents_id_tenant_id"),
        Index("ix_documents_tenant_id", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    document_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    issuer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(String(4000), nullable=True)

    tenant: Mapped["Tenant"] = relationship(back_populates="documents")
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", overlaps="tenant"
    )
