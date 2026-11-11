"""add adventure records

Revision ID: 0032_adventure_records
Revises: 0031_authored_map_visibility
Create Date: 2026-11-11 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0032_adventure_records'
down_revision = '0031_authored_map_visibility'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'adventure_records',
        sa.Column('record_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('owner_key', sa.String(length=120), nullable=False),
        sa.Column('pack_id', sa.String(length=120), nullable=False),
        sa.Column('pack_title', sa.String(length=160), server_default='', nullable=False),
        sa.Column('endings_json', sa.Text(), server_default='[]', nullable=False),
        sa.Column('secrets_found_json', sa.Text(), server_default='[]', nullable=False),
        sa.Column('secrets_total', sa.Integer(), server_default='0', nullable=False),
        sa.Column('completions_json', sa.Text(), server_default='[]', nullable=False),
        sa.Column('times_completed', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('record_id', name=op.f('pk_adventure_records')),
        sa.UniqueConstraint('owner_key', 'pack_id', name='uq_adventure_records_owner_pack'),
    )
    with op.batch_alter_table('adventure_records', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_adventure_records_owner_key'), ['owner_key'], unique=False)


def downgrade():
    with op.batch_alter_table('adventure_records', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_adventure_records_owner_key'))
    op.drop_table('adventure_records')
