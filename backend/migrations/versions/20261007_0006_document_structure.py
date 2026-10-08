"""Persist Docling document structure and provenance.

Revision ID: 20261007_0006
Revises: 20261007_0005
Create Date: 2026-10-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261007_0006"
down_revision: Union[str, None] = "20261007_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column("structure_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.create_table(
        "document_pages",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("width", sa.Float(), nullable=True),
        sa.Column("height", sa.Float(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("page_number > 0", name="document_page_number_positive"),
        sa.CheckConstraint("width IS NULL OR width > 0", name="document_page_width_positive"),
        sa.CheckConstraint("height IS NULL OR height > 0", name="document_page_height_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_document_pages_tenant", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["document_id", "tenant_id"], ["documents.id", "documents.tenant_id"],
            name="fk_document_pages_document_tenant", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id", "document_id", "tenant_id"],
            ["document_versions.id", "document_versions.document_id", "document_versions.tenant_id"],
            name="fk_document_pages_version_document_tenant", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_pages"),
        sa.UniqueConstraint("document_version_id", "page_number", name="uq_document_pages_version_number"),
        sa.UniqueConstraint("id", "document_version_id", "document_id", "tenant_id", name="uq_document_pages_scope"),
    )
    op.create_index("ix_document_pages_tenant_id", "document_pages", ["tenant_id"])

    op.create_table(
        "document_sections",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parent_section_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_ref", sa.String(length=200), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("source_provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("ordinal >= 0", name="document_section_ordinal_nonnegative"),
        sa.CheckConstraint("level > 0", name="document_section_level_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_document_sections_tenant", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["document_id", "tenant_id"], ["documents.id", "documents.tenant_id"],
            name="fk_document_sections_document_tenant", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id", "document_id", "tenant_id"],
            ["document_versions.id", "document_versions.document_id", "document_versions.tenant_id"],
            name="fk_document_sections_version_document_tenant", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["page_id", "document_version_id", "document_id", "tenant_id"],
            ["document_pages.id", "document_pages.document_version_id", "document_pages.document_id", "document_pages.tenant_id"],
            name="fk_document_sections_page_scope", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_section_id", "document_version_id", "document_id", "tenant_id"],
            ["document_sections.id", "document_sections.document_version_id", "document_sections.document_id", "document_sections.tenant_id"],
            name="fk_document_sections_parent_scope", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_sections"),
        sa.UniqueConstraint("document_version_id", "source_ref", name="uq_document_sections_version_source"),
        sa.UniqueConstraint("id", "document_version_id", "document_id", "tenant_id", name="uq_document_sections_scope"),
    )
    op.create_index("ix_document_sections_tenant_id", "document_sections", ["tenant_id"])

    op.create_table(
        "document_elements",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_ref", sa.String(length=200), nullable=False),
        sa.Column("element_type", sa.String(length=20), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("structured_content", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("ordinal >= 0", name="document_element_ordinal_nonnegative"),
        sa.CheckConstraint("element_type IN ('TEXT', 'TABLE', 'FIGURE')", name="document_element_type_valid"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_document_elements_tenant", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["document_id", "tenant_id"], ["documents.id", "documents.tenant_id"],
            name="fk_document_elements_document_tenant", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id", "document_id", "tenant_id"],
            ["document_versions.id", "document_versions.document_id", "document_versions.tenant_id"],
            name="fk_document_elements_version_document_tenant", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["page_id", "document_version_id", "document_id", "tenant_id"],
            ["document_pages.id", "document_pages.document_version_id", "document_pages.document_id", "document_pages.tenant_id"],
            name="fk_document_elements_page_scope", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["section_id", "document_version_id", "document_id", "tenant_id"],
            ["document_sections.id", "document_sections.document_version_id", "document_sections.document_id", "document_sections.tenant_id"],
            name="fk_document_elements_section_scope", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_elements"),
        sa.UniqueConstraint("document_version_id", "ordinal", name="uq_document_elements_version_ordinal"),
    )
    op.create_index("ix_document_elements_tenant_id", "document_elements", ["tenant_id"])
    op.create_index("ix_document_elements_version_id", "document_elements", ["document_version_id"])


def downgrade() -> None:
    op.drop_index("ix_document_elements_version_id", table_name="document_elements")
    op.drop_index("ix_document_elements_tenant_id", table_name="document_elements")
    op.drop_table("document_elements")
    op.drop_index("ix_document_sections_tenant_id", table_name="document_sections")
    op.drop_table("document_sections")
    op.drop_index("ix_document_pages_tenant_id", table_name="document_pages")
    op.drop_table("document_pages")
    op.drop_column("document_versions", "structure_metadata")
