"""Tenant- and version-scoped canonical document structure records."""

import uuid

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.models.mixins import TimestampMixin, UUIDPrimaryKey

JSON_VALUE = JSON().with_variant(JSONB(), "postgresql")


class DocumentPage(UUIDPrimaryKey, TimestampMixin, Base):
    """A page number and optional dimensions reported by the parser."""

    __tablename__ = "document_pages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="fk_document_pages_tenant", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "tenant_id"],
            ["documents.id", "documents.tenant_id"],
            name="fk_document_pages_document_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_version_id", "document_id", "tenant_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.tenant_id",
            ],
            name="fk_document_pages_version_document_tenant",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "document_version_id", "page_number", name="uq_document_pages_version_number"
        ),
        UniqueConstraint(
            "id", "document_version_id", "document_id", "tenant_id",
            name="uq_document_pages_scope",
        ),
        CheckConstraint("page_number > 0", name="document_page_number_positive"),
        CheckConstraint("width IS NULL OR width > 0", name="document_page_width_positive"),
        CheckConstraint("height IS NULL OR height > 0", name="document_page_height_positive"),
        Index("ix_document_pages_tenant_id", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    document_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[float | None] = mapped_column(Float, nullable=True)
    height: Mapped[float | None] = mapped_column(Float, nullable=True)


class DocumentSection(UUIDPrimaryKey, TimestampMixin, Base):
    """A parser-reported section header and its source hierarchy reference."""

    __tablename__ = "document_sections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="fk_document_sections_tenant", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "tenant_id"],
            ["documents.id", "documents.tenant_id"],
            name="fk_document_sections_document_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_version_id", "document_id", "tenant_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.tenant_id",
            ],
            name="fk_document_sections_version_document_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["page_id", "document_version_id", "document_id", "tenant_id"],
            [
                "document_pages.id",
                "document_pages.document_version_id",
                "document_pages.document_id",
                "document_pages.tenant_id",
            ],
            name="fk_document_sections_page_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["parent_section_id", "document_version_id", "document_id", "tenant_id"],
            [
                "document_sections.id",
                "document_sections.document_version_id",
                "document_sections.document_id",
                "document_sections.tenant_id",
            ],
            name="fk_document_sections_parent_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "document_version_id", "source_ref", name="uq_document_sections_version_source"
        ),
        UniqueConstraint(
            "id", "document_version_id", "document_id", "tenant_id",
            name="uq_document_sections_scope",
        ),
        CheckConstraint("ordinal >= 0", name="document_section_ordinal_nonnegative"),
        CheckConstraint("level > 0", name="document_section_level_positive"),
        Index("ix_document_sections_tenant_id", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    document_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    page_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    parent_section_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    source_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    source_provenance: Mapped[list[dict]] = mapped_column(
        JSON_VALUE, nullable=False, default=list
    )


class DocumentElement(UUIDPrimaryKey, TimestampMixin, Base):
    """Canonical text/table/figure content with parser source provenance."""

    __tablename__ = "document_elements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="fk_document_elements_tenant", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "tenant_id"],
            ["documents.id", "documents.tenant_id"],
            name="fk_document_elements_document_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_version_id", "document_id", "tenant_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.tenant_id",
            ],
            name="fk_document_elements_version_document_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["page_id", "document_version_id", "document_id", "tenant_id"],
            [
                "document_pages.id",
                "document_pages.document_version_id",
                "document_pages.document_id",
                "document_pages.tenant_id",
            ],
            name="fk_document_elements_page_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["section_id", "document_version_id", "document_id", "tenant_id"],
            [
                "document_sections.id",
                "document_sections.document_version_id",
                "document_sections.document_id",
                "document_sections.tenant_id",
            ],
            name="fk_document_elements_section_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "document_version_id", "ordinal", name="uq_document_elements_version_ordinal"
        ),
        UniqueConstraint(
            "id", "document_version_id", "document_id", "tenant_id",
            name="uq_document_elements_scope",
        ),
        CheckConstraint("ordinal >= 0", name="document_element_ordinal_nonnegative"),
        CheckConstraint(
            "element_type IN ('TEXT', 'TABLE', 'FIGURE')",
            name="document_element_type_valid",
        ),
        Index("ix_document_elements_tenant_id", "tenant_id"),
        Index("ix_document_elements_version_id", "document_version_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    document_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    page_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    section_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    source_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    element_type: Mapped[str] = mapped_column(String(20), nullable=False)
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_content: Mapped[dict | None] = mapped_column(JSON_VALUE, nullable=True)
    source_provenance: Mapped[list[dict]] = mapped_column(
        JSON_VALUE, nullable=False, default=list
    )
