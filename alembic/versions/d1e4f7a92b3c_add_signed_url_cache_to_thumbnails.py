"""add_signed_url_cache_to_thumbnails

Revision ID: d1e4f7a92b3c
Revises: c4d28e83f152
Create Date: 2026-03-06 12:00:00.000000

Adds signed_url and signed_url_expires_at columns to the thumbnails table
so the backend can cache GCS signed URLs and reuse them until they expire,
avoiding repeated GCS signing calls on every thumbnail request.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1e4f7a92b3c'
down_revision: Union[str, Sequence[str], None] = 'c4d28e83f152'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add signed_url cache columns to thumbnails."""
    op.add_column('thumbnails', sa.Column('signed_url', sa.Text(), nullable=True))
    op.add_column('thumbnails', sa.Column('signed_url_expires_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Remove signed_url cache columns from thumbnails."""
    op.drop_column('thumbnails', 'signed_url_expires_at')
    op.drop_column('thumbnails', 'signed_url')
