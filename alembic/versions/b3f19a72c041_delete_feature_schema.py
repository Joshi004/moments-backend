"""delete_feature_schema

Revision ID: b3f19a72c041
Revises: a5fe25dd5741
Create Date: 2026-02-26 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f19a72c041'
down_revision: Union[str, Sequence[str], None] = 'a5fe25dd5741'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    # Step 1 — Create audios table (1:1 with videos, ON DELETE CASCADE)
    op.create_table(
        'audios',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('video_id', sa.Integer(), nullable=False),
        sa.Column('cloud_url', sa.Text(), nullable=False),
        sa.Column('file_size_kb', sa.BigInteger(), nullable=True),
        sa.Column('format', sa.String(length=20), nullable=True),
        sa.Column('sample_rate', sa.Integer(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['video_id'], ['videos.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('video_id'),
    )
    op.create_index(op.f('ix_audios_video_id'), 'audios', ['video_id'], unique=False)

    # Step 2 — Make videos.cloud_url nullable (required for scope=video_file delete)
    op.alter_column(
        'videos', 'cloud_url',
        existing_type=sa.Text(),
        nullable=True,
    )

    # Step 3 — Change moments.parent_id FK from SET NULL to CASCADE
    # Aligns DB behavior with the ORM cascade="all" on the children relationship
    op.drop_constraint('moments_parent_id_fkey', 'moments', type_='foreignkey')
    op.create_foreign_key(
        'moments_parent_id_fkey',
        'moments', 'moments',
        ['parent_id'], ['id'],
        ondelete='CASCADE',
    )

    # Step 4 — Change generation_configs.transcript_id FK from CASCADE to SET NULL
    # Prevents deleting a transcript from destroying shared GenerationConfig records
    op.drop_constraint('generation_configs_transcript_id_fkey', 'generation_configs', type_='foreignkey')
    op.create_foreign_key(
        'generation_configs_transcript_id_fkey',
        'generation_configs', 'transcripts',
        ['transcript_id'], ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema — reverses upgrade() in reverse order."""

    # Step 4 reversed — restore generation_configs.transcript_id FK to CASCADE
    op.drop_constraint('generation_configs_transcript_id_fkey', 'generation_configs', type_='foreignkey')
    op.create_foreign_key(
        'generation_configs_transcript_id_fkey',
        'generation_configs', 'transcripts',
        ['transcript_id'], ['id'],
        ondelete='CASCADE',
    )

    # Step 3 reversed — restore moments.parent_id FK to SET NULL
    op.drop_constraint('moments_parent_id_fkey', 'moments', type_='foreignkey')
    op.create_foreign_key(
        'moments_parent_id_fkey',
        'moments', 'moments',
        ['parent_id'], ['id'],
        ondelete='SET NULL',
    )

    # Step 2 reversed — restore videos.cloud_url to NOT NULL
    op.alter_column(
        'videos', 'cloud_url',
        existing_type=sa.Text(),
        nullable=False,
    )

    # Step 1 reversed — drop audios table
    op.drop_index(op.f('ix_audios_video_id'), table_name='audios')
    op.drop_table('audios')
