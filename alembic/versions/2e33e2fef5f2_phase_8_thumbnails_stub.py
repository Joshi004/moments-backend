"""phase_8_thumbnails_stub

Revision ID: 2e33e2fef5f2
Revises: 21c36b3a2038
Create Date: 2026-02-13 00:00:00.000000

This is a stub migration representing schema changes applied during Phase 8
(Thumbnails to Cloud and Database). The actual DDL was applied directly;
this file exists to keep Alembic's version tracking consistent.
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = '2e33e2fef5f2'
down_revision: Union[str, Sequence[str], None] = '21c36b3a2038'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
