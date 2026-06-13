"""campaign segment source identity

Revision ID: 0023_campaign_segment_source_identity
Revises: 0022_installed_campaign_packs
Create Date: 2026-06-12 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = '0023_campaign_segment_source_identity'
down_revision = '0022_installed_campaign_packs'
branch_labels = None
depends_on = None


def _columns(table_name):
    return {column['name'] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _index_names(table_name):
    return {index['name'] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade():
    columns = _columns('campaign_segments')
    with op.batch_alter_table('campaign_segments', schema=None) as batch_op:
        if 'external_id' not in columns:
            batch_op.add_column(sa.Column('external_id', sa.String(length=120), nullable=True))
        if 'source' not in columns:
            batch_op.add_column(sa.Column('source', sa.String(length=40), nullable=False, server_default='authored'))
        if 'source_pack_id' not in columns:
            batch_op.add_column(sa.Column('source_pack_id', sa.String(length=120), nullable=True))
        if 'metadata_json' not in columns:
            batch_op.add_column(sa.Column('metadata_json', sa.Text(), nullable=True))
    if 'source' not in columns:
        op.execute("UPDATE campaign_segments SET source = 'authored' WHERE source IS NULL OR source = ''")
        with op.batch_alter_table('campaign_segments', schema=None) as batch_op:
            batch_op.alter_column('source', server_default=None)
    if 'ix_campaign_segments_campaign_source_external' not in _index_names('campaign_segments'):
        op.create_index(
            'ix_campaign_segments_campaign_source_external',
            'campaign_segments',
            ['campaign_id', 'source', 'external_id'],
            unique=False,
        )


def downgrade():
    if 'ix_campaign_segments_campaign_source_external' in _index_names('campaign_segments'):
        op.drop_index('ix_campaign_segments_campaign_source_external', table_name='campaign_segments')
    columns = _columns('campaign_segments')
    with op.batch_alter_table('campaign_segments', schema=None) as batch_op:
        if 'metadata_json' in columns:
            batch_op.drop_column('metadata_json')
        if 'source_pack_id' in columns:
            batch_op.drop_column('source_pack_id')
        if 'source' in columns:
            batch_op.drop_column('source')
        if 'external_id' in columns:
            batch_op.drop_column('external_id')
