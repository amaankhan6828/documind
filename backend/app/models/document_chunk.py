"""Canonical tenant- and version-scoped retrieval-ready chunks."""

import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.models.mixins import TimestampMixin, UUIDPrimaryKey


class DocumentChunk(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="fk_document_chunks_tenant", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "tenant_id"],
            ["documents.id", "documents.tenant_id"],
            name="fk_document_chunks_document_tenant", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_version_id", "document_id", "tenant_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.tenant_id",
            ],
            name="fk_document_chunks_version_document_tenant",
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
            name="fk_document_chunks_section_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "document_version_id", "ordinal", name="uq_document_chunks_version_ordinal"
        ),
        UniqueConstraint(
            "id", "document_version_id", "document_id", "tenant_id",
            name="uq_document_chunks_scope",
        ),
        CheckConstraint("ordinal >= 0", name="document_chunk_ordinal_nonnegative"),
        CheckConstraint("character_length > 0", name="document_chunk_length_positive"),
        CheckConstraint("length(content_hash) = 64", name="document_chunk_hash_sha256_length"),
        Index("ix_document_chunks_tenant_id", "tenant_id"),
        Index("ix_document_chunks_version_id", "document_version_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    document_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    section_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    character_length: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class DocumentChunkSource(Base):
    """An ordered link from a chunk to one element provenance location."""

    __tablename__ = "document_chunk_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chunk_id", "document_version_id", "document_id", "tenant_id"],
            [
                "document_chunks.id",
                "document_chunks.document_version_id",
                "document_chunks.document_id",
                "document_chunks.tenant_id",
            ],
            name="fk_document_chunk_sources_chunk_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_element_id", "document_version_id", "document_id", "tenant_id"],
            [
                "document_elements.id",
                "document_elements.document_version_id",
                "document_elements.document_id",
                "document_elements.tenant_id",
            ],
            name="fk_document_chunk_sources_element_scope",
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
            name="fk_document_chunk_sources_page_scope",
            ondelete="CASCADE",
        ),
        CheckConstraint("source_ordinal >= 0", name="chunk_source_ordinal_nonnegative"),
        CheckConstraint("location_ordinal >= 0", name="chunk_source_location_nonnegative"),
        CheckConstraint("page_number IS NULL OR page_number > 0", name="chunk_source_page_positive"),
        Index("ix_document_chunk_sources_element_id", "document_element_id"),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    source_ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    location_ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    document_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    document_element_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    page_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    element_type: Mapped[str] = mapped_column(String(20), nullable=False)
