"""Add tenant-scoped processing jobs.

Revision ID: 20261007_0005
Revises: 20261007_0004
Create Date: 2026-10-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261007_0005"
down_revision: Union[str, None] = "20261007_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_document_versions_id_document_tenant",
        "document_versions",
        ["id", "document_id", "tenant_id"],
    )
    op.create_table(
        "processing_jobs",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "job_type = 'DOCUMENT_PROCESSING'", name="processing_job_type_valid"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')",
            name="processing_job_status_valid",
        ),
        sa.CheckConstraint("attempt >= 0", name="processing_job_attempt_nonnegative"),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 100", name="processing_job_progress_range"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_processing_jobs_tenant_id_tenants", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "tenant_id"],
            ["documents.id", "documents.tenant_id"],
            name="fk_processing_jobs_document_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id", "document_id", "tenant_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.tenant_id",
            ],
            name="fk_processing_jobs_version_document_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_processing_jobs"),
        sa.UniqueConstraint(
            "document_version_id", "job_type", name="uq_processing_jobs_version_type"
        ),
    )
    op.create_index(
        "ix_processing_jobs_tenant_status",
        "processing_jobs",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_processing_jobs_document_id", "processing_jobs", ["document_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_processing_jobs_document_id", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_tenant_status", table_name="processing_jobs")
    op.drop_table("processing_jobs")
    op.drop_constraint(
        "uq_document_versions_id_document_tenant",
        "document_versions",
        type_="unique",
    )
