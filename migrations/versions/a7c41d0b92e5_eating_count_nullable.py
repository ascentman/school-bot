"""eating_count nullable

Харчування більше не обліковується: вчитель його не вводить, і в жодному звіті
воно не друкується. Колонку не видаляємо — за попередні місяці в ній лежать
справжні цифри, і вони мусять лишитися читними. Достатньо зняти NOT NULL, щоб
нові записи (лише відсутні й хворі) взагалі могли існувати.

Revision ID: a7c41d0b92e5
Revises: e32a7fd85849
Create Date: 2026-10-06 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7c41d0b92e5'
down_revision: Union[str, Sequence[str], None] = 'e32a7fd85849'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # batch_alter_table: SQLite не вміє ALTER COLUMN, тож alembic перебудовує
    # таблицю. Дані при цьому переносяться — історія харчування лишається.
    with op.batch_alter_table('meal_entry', schema=None) as batch_op:
        batch_op.alter_column('eating_count', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    """Downgrade schema.

    Повернути NOT NULL можна лише тоді, коли порожніх цифр харчування немає:
    інакше перебудова таблиці впаде на першому ж записі, створеному після
    відмови від обліку. Тому спершу заповнюємо їх нулями.
    """
    op.execute('UPDATE meal_entry SET eating_count = 0 WHERE eating_count IS NULL')
    with op.batch_alter_table('meal_entry', schema=None) as batch_op:
        batch_op.alter_column('eating_count', existing_type=sa.Integer(), nullable=False)
