"""Add stored-file metadata to document versions.

Revision ID: 20261007_0004
Revises: 20261007_0003
Create Date: 2026-10-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261007_0004"
down_revision: Union[str, None] = "20261007_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column("original_filename", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "document_versions",
        sa.Column("storage_key", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "document_versions",
        sa.Column("mime_type", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "document_versions", sa.Column("file_size", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "document_versions", sa.Column("sha256", sa.String(length=64), nullable=True)
    )
    op.create_unique_constraint(
        "uq_document_versions_document_sha256",
        "document_versions",
        ["document_id", "sha256"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_document_versions_document_sha256", "document_versions", type_="unique"
    )
    op.drop_column("document_versions", "sha256")
    op.drop_column("document_versions", "file_size")
    op.drop_column("document_versions", "mime_type")
    op.drop_column("document_versions", "storage_key")
    op.drop_column("document_versions", "original_filename")
