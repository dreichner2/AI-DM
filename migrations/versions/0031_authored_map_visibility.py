"""add authored map visibility

Revision ID: 0031_authored_map_visibility
Revises: 0030_player_weapon_proficiencies
Create Date: 2026-07-11 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0031_authored_map_visibility'
down_revision = '0030_player_weapon_proficiencies'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('maps', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'visibility',
                sa.String(length=16),
                server_default='player',
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            op.f('ck_maps_maps_visibility'),
            "visibility IN ('player', 'dm')",
        )


def downgrade():
    with op.batch_alter_table('maps', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_maps_maps_visibility'), type_='check')
        batch_op.drop_column('visibility')
