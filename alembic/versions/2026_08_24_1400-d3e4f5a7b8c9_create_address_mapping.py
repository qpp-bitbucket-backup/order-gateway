"""create address_mapping table and seed province codes

Revision ID: d3e4f5a7b8c9
Revises: c2d3e4f5a7b8
Create Date: 2026-08-24 14:00:00.000000

Seed data source: docs/PS.CN运费.xlsx (StateCode/StateDesc columns),
with common English province names added for lookups.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd3e4f5a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c2d3e4f5a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (state_code, state_desc, state_name_en) — from docs/PS.CN运费.xlsx
PROVINCES = [
    ("110000", "北京市", "Beijing"),
    ("120000", "天津市", "Tianjin"),
    ("130000", "河北省", "Hebei"),
    ("140000", "山西省", "Shanxi"),
    ("150000", "内蒙古自治区", "Inner Mongolia"),
    ("210000", "辽宁省", "Liaoning"),
    ("220000", "吉林省", "Jilin"),
    ("230000", "黑龙江省", "Heilongjiang"),
    ("310000", "上海市", "Shanghai"),
    ("320000", "江苏省", "Jiangsu"),
    ("330000", "浙江省", "Zhejiang"),
    ("340000", "安徽省", "Anhui"),
    ("350000", "福建省", "Fujian"),
    ("360000", "江西省", "Jiangxi"),
    ("370000", "山东省", "Shandong"),
    ("410000", "河南省", "Henan"),
    ("420000", "湖北省", "Hubei"),
    ("430000", "湖南省", "Hunan"),
    ("440000", "广东省", "Guangdong"),
    ("450000", "广西壮族自治区", "Guangxi"),
    ("460000", "海南省", "Hainan"),
    ("500000", "重庆市", "Chongqing"),
    ("510000", "四川省", "Sichuan"),
    ("520000", "贵州省", "Guizhou"),
    ("530000", "云南省", "Yunnan"),
    ("540000", "西藏自治区", "Tibet"),
    ("610000", "陕西省", "Shaanxi"),
    ("620000", "甘肃省", "Gansu"),
    ("630000", "青海省", "Qinghai"),
    ("640000", "宁夏回族自治区", "Ningxia"),
    ("650000", "新疆维吾尔自治区", "Xinjiang"),
    ("710000", "台湾省", "Taiwan"),
    ("810000", "香港特别行政区", "Hong Kong"),
    ("820000", "澳门特别行政区", "Macau"),
]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'address_mapping',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('state_code', sa.String(length=12), nullable=False),
        sa.Column('state_desc', sa.String(length=64), nullable=False),
        sa.Column('state_name_en', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('state_code'),
        sa.UniqueConstraint('state_desc'),
    )
    op.create_index(op.f('ix_address_mapping_state_code'), 'address_mapping', ['state_code'], unique=False)
    op.create_index(op.f('ix_address_mapping_state_desc'), 'address_mapping', ['state_desc'], unique=False)
    op.create_index(op.f('ix_address_mapping_state_name_en'), 'address_mapping', ['state_name_en'], unique=False)

    address_mapping = sa.table(
        'address_mapping',
        sa.column('created_at', sa.DateTime),
        sa.column('updated_at', sa.DateTime),
        sa.column('is_active', sa.Boolean),
        sa.column('state_code', sa.String),
        sa.column('state_desc', sa.String),
        sa.column('state_name_en', sa.String),
    )
    now = sa.text('NOW()')
    op.bulk_insert(address_mapping, [
        {
            'state_code': code,
            'state_desc': desc,
            'state_name_en': en,
            'is_active': True,
            'created_at': now,
            'updated_at': now,
        }
        for code, desc, en in PROVINCES
    ])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_address_mapping_state_name_en'), table_name='address_mapping')
    op.drop_index(op.f('ix_address_mapping_state_desc'), table_name='address_mapping')
    op.drop_index(op.f('ix_address_mapping_state_code'), table_name='address_mapping')
    op.drop_table('address_mapping')
