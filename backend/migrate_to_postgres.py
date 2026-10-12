"""Move a stopped Windows SQLite installation into an empty PostgreSQL schema.

Run only after ``alembic upgrade head`` on the destination. The source SQLite
file should be a final, consistent backup taken after stopping the local app.
Windows DPAPI credentials and live sessions cannot be reused on Linux.
"""

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import Integer, create_engine, func, select, text

from app.db import Base


EXCLUDED_TABLES = {'alembic_version', 'wb_accounts', 'sessions', 'jobs', 'actions', 'publication_jobs'}
EXCLUDED_SETTINGS = {'automation_schedule', 'scheduler_status', 'portable_sync'}


def copy_database(source_path: Path, destination) -> dict[str, int]:
    if not source_path.is_file() or source_path.suffix.lower() != '.db':
        raise ValueError('Укажите существующую копию SQLite с расширением .db')

    source = create_engine('sqlite:///' + source_path.resolve().as_posix())
    counts = {}
    try:
        with source.connect() as src, destination.begin() as dst:
            if src.exec_driver_sql('PRAGMA quick_check').scalar_one() != 'ok':
                raise ValueError('Исходная база не прошла проверку целостности')
            for table in Base.metadata.sorted_tables:
                if table.name in EXCLUDED_TABLES:
                    continue
                if dst.execute(select(func.count()).select_from(table)).scalar_one():
                    raise ValueError(f'Таблица назначения {table.name} не пуста')

            for table in Base.metadata.sorted_tables:
                if table.name in EXCLUDED_TABLES:
                    continue
                copied = 0
                rows = src.execute(select(table)).mappings()
                while batch := rows.fetchmany(500):
                    values = [dict(row) for row in batch]
                    if table.name == 'app_settings':
                        values = [row for row in values if row['key'] not in EXCLUDED_SETTINGS]
                    if values:
                        dst.execute(table.insert(), values)
                        copied += len(values)
                counts[table.name] = copied

            settings = Base.metadata.tables['app_settings']
            for key in ('safe_mode', 'test_mode'):
                dst.execute(settings.delete().where(settings.c.key == key))
                dst.execute(settings.insert().values(key=key, value=True))

            # Explicit integer IDs need their PostgreSQL sequences advanced.
            for table in Base.metadata.sorted_tables:
                if table.name in EXCLUDED_TABLES or len(table.primary_key.columns) != 1:
                    continue
                column = next(iter(table.primary_key.columns))
                if destination.dialect.name != 'postgresql' or not column.autoincrement or not isinstance(column.type, Integer):
                    continue
                sequence = dst.execute(
                    text('SELECT pg_get_serial_sequence(:table_name, :column_name)'),
                    {'table_name': table.name, 'column_name': column.name},
                ).scalar_one_or_none()
                if sequence:
                    maximum = dst.execute(select(func.max(column))).scalar_one()
                    if maximum is not None:
                        dst.execute(text('SELECT setval(CAST(:sequence AS regclass), :value)'),
                                    {'sequence': sequence, 'value': maximum})
    finally:
        source.dispose()
    return counts


def transfer(source_path: Path, destination_url: str) -> dict[str, int]:
    if not destination_url.startswith(('postgresql+psycopg://', 'postgresql://')):
        raise ValueError('Назначением должен быть PostgreSQL')
    destination = create_engine(destination_url, pool_pre_ping=True)
    try:
        return copy_database(source_path, destination)
    finally:
        destination.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sqlite_backup', type=Path)
    args = parser.parse_args()
    destination_url = os.environ.get('DATABASE_URL', '')
    if not destination_url:
        sys.exit('DATABASE_URL не задан')
    for name, count in transfer(args.sqlite_backup, destination_url).items():
        print(f'{name}: {count}')


if __name__ == '__main__':
    main()
