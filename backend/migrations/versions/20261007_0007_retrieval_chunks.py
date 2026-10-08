"""Persist canonical retrieval-ready chunks and source-element links."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261007_0007"
down_revision = "20261007_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_document_elements_scope",
        "document_elements",
        ["id", "document_version_id", "document_id", "tenant_id"],
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("character_length", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("ordinal >= 0", name="document_chunk_ordinal_nonnegative"),
        sa.CheckConstraint("character_length > 0", name="document_chunk_length_positive"),
        sa.CheckConstraint("length(content_hash) = 64", name="document_chunk_hash_sha256_length"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_document_chunks_tenant", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id", "tenant_id"], ["documents.id", "documents.tenant_id"], name="fk_document_chunks_document_tenant", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_version_id", "document_id", "tenant_id"], ["document_versions.id", "document_versions.document_id", "document_versions.tenant_id"], name="fk_document_chunks_version_document_tenant", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["section_id", "document_version_id", "document_id", "tenant_id"], ["document_sections.id", "document_sections.document_version_id", "document_sections.document_id", "document_sections.tenant_id"], name="fk_document_chunks_section_scope", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_document_chunks"),
        sa.UniqueConstraint("document_version_id", "ordinal", name="uq_document_chunks_version_ordinal"),
        sa.UniqueConstraint("id", "document_version_id", "document_id", "tenant_id", name="uq_document_chunks_scope"),
    )
    op.create_index("ix_document_chunks_tenant_id", "document_chunks", ["tenant_id"])
    op.create_index("ix_document_chunks_version_id", "document_chunks", ["document_version_id"])
    op.create_table(
        "document_chunk_sources",
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_ordinal", sa.Integer(), nullable=False),
        sa.Column("location_ordinal", sa.Integer(), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_element_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("source_ref", sa.String(length=200), nullable=False),
        sa.Column("element_type", sa.String(length=20), nullable=False),
        sa.CheckConstraint("source_ordinal >= 0", name="chunk_source_ordinal_nonnegative"),
        sa.CheckConstraint("location_ordinal >= 0", name="chunk_source_location_nonnegative"),
        sa.CheckConstraint("page_number IS NULL OR page_number > 0", name="chunk_source_page_positive"),
        sa.ForeignKeyConstraint(["chunk_id", "document_version_id", "document_id", "tenant_id"], ["document_chunks.id", "document_chunks.document_version_id", "document_chunks.document_id", "document_chunks.tenant_id"], name="fk_document_chunk_sources_chunk_scope", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_element_id", "document_version_id", "document_id", "tenant_id"], ["document_elements.id", "document_elements.document_version_id", "document_elements.document_id", "document_elements.tenant_id"], name="fk_document_chunk_sources_element_scope", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["page_id", "document_version_id", "document_id", "tenant_id"], ["document_pages.id", "document_pages.document_version_id", "document_pages.document_id", "document_pages.tenant_id"], name="fk_document_chunk_sources_page_scope", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("chunk_id", "source_ordinal", "location_ordinal", name="pk_document_chunk_sources"),
    )
    op.create_index("ix_document_chunk_sources_element_id", "document_chunk_sources", ["document_element_id"])


def downgrade() -> None:
    op.drop_index("ix_document_chunk_sources_element_id", table_name="document_chunk_sources")
    op.drop_table("document_chunk_sources")
    op.drop_index("ix_document_chunks_version_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_tenant_id", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_constraint("uq_document_elements_scope", "document_elements", type_="unique")
