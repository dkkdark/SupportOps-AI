"""Initial application schema and pgvector extension.

Revision ID: 001
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision='001'
down_revision=None
branch_labels=None
depends_on=None


def upgrade():
    pg=op.get_bind().dialect.name=='postgresql'
    if pg:op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    op.create_table('documents',sa.Column('id',sa.String(80),primary_key=True),sa.Column('key',sa.String(80),nullable=False),sa.Column('title',sa.String(180),nullable=False),sa.Column('category',sa.String(24),nullable=False),sa.Column('version',sa.String(30),nullable=False),sa.Column('effective_from',sa.String(10),nullable=False),sa.Column('effective_to',sa.String(10)),sa.Column('text',sa.Text,nullable=False),sa.Column('tags',sa.JSON,nullable=False),sa.Column('facts',sa.JSON,nullable=False),sa.Column('embedding',Vector(128) if pg else sa.JSON,nullable=False))
    op.create_index('ix_documents_key','documents',['key'])
    op.create_table('tickets',sa.Column('id',sa.String(40),primary_key=True),sa.Column('customer',sa.String(100),nullable=False),sa.Column('email',sa.String(160),nullable=False),sa.Column('subject',sa.String(180),nullable=False),sa.Column('message',sa.Text,nullable=False),sa.Column('order_id',sa.String(20)),sa.Column('channel',sa.String(20),nullable=False),sa.Column('priority',sa.String(20),nullable=False),sa.Column('status',sa.String(24),nullable=False),sa.Column('category',sa.String(24),nullable=False),sa.Column('result',sa.JSON),sa.Column('revision',sa.Integer,nullable=False),sa.Column('idempotency_key',sa.String(100),unique=True),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_tickets_status','tickets',['status'])
    op.create_table('reviews',sa.Column('id',sa.String(40),primary_key=True),sa.Column('ticket_id',sa.String(40),sa.ForeignKey('tickets.id'),nullable=False),sa.Column('decision',sa.String(20),nullable=False),sa.Column('text',sa.Text,nullable=False),sa.Column('note',sa.Text,nullable=False),sa.Column('actor',sa.String(80),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_reviews_ticket_id','reviews',['ticket_id'])
    if pg:op.execute('CREATE INDEX ix_documents_embedding ON documents USING hnsw (embedding vector_cosine_ops)')


def downgrade():
    op.drop_table('reviews')
    op.drop_table('tickets')
    op.drop_table('documents')
