"""make_audio_cloud_url_nullable

Revision ID: c4d28e83f152
Revises: b3f19a72c041
Create Date: 2026-02-26 13:00:00.000000

Makes audios.cloud_url nullable so that scope=video_file can set it to NULL
after the GCS file is deleted (keeping the Audio DB record intact).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d28e83f152'
down_revision: Union[str, Sequence[str], None] = 'b3f19a72c041'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Allow audios.cloud_url to be NULL."""
    op.alter_column(
        'audios', 'cloud_url',
        existing_type=sa.Text(),
        nullable=True,
    )


def downgrade() -> None:
    """Restore audios.cloud_url to NOT NULL.

    NOTE: This will fail if any rows have a NULL cloud_url.
    Clear NULL values before running downgrade.
    """
    op.alter_column(
        'audios', 'cloud_url',
        existing_type=sa.Text(),
        nullable=False,
    )
