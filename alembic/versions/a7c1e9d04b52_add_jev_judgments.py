"""Add Jev display-suitability judgment columns

Nullable, no backfill: NULL jev_scored_at means the artwork is unjudged and the
Backend keeps using its keyword heuristic for it.

Revision ID: a7c1e9d04b52
Revises: 131fd44199da
Create Date: 2026-10-10 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c1e9d04b52"
down_revision: Union[str, None] = "131fd44199da"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("artworks") as batch:
        batch.add_column(sa.Column("jev_flat_display_p", sa.Float(), nullable=True))
        batch.add_column(sa.Column("jev_fragment_p", sa.Float(), nullable=True))
        batch.add_column(sa.Column("jev_attribution", sa.String(length=30), nullable=True))
        batch.add_column(sa.Column("jev_appeal", sa.Float(), nullable=True))
        batch.add_column(sa.Column("jev_model", sa.String(length=50), nullable=True))
        batch.add_column(sa.Column("jev_scored_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("artworks") as batch:
        for col in (
            "jev_scored_at",
            "jev_model",
            "jev_appeal",
            "jev_attribution",
            "jev_fragment_p",
            "jev_flat_display_p",
        ):
            batch.drop_column(col)
