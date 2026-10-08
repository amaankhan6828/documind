"""Add logical document metadata fields.

Revision ID: 20261007_0003
Revises: 20261007_0002
Create Date: 2026-10-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261007_0003"
down_revision: Union[str, None] = "20261007_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("document_type", sa.String(length=100), nullable=True))
    op.add_column("documents", sa.Column("issuer", sa.String(length=200), nullable=True))
    op.add_column("documents", sa.Column("description", sa.String(length=4000), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "description")
    op.drop_column("documents", "issuer")
    op.drop_column("documents", "document_type")
