import argparse
import getpass
import sqlite3
import os
from datetime import datetime
from pathlib import Path
from argon2 import PasswordHasher
from .config import DATA, DATABASE
from .db import Session, Setting, setting, set_setting


def backup():
    destination = DATA / 'backups' / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.db')
    with sqlite3.connect(DATABASE) as source, sqlite3.connect(destination) as target:
        source.backup(target)
        target.execute('PRAGMA secure_delete=ON')
        # Exclude credentials and browser sessions from portable backups.
        target.execute('DELETE FROM wb_accounts')
        target.execute('DELETE FROM sessions')
        target.execute("DELETE FROM app_settings WHERE key='password_hash'")
        target.commit()
        target.execute('VACUUM')
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Проверка резервной копии не пройдена')
    return destination.name


def restore(name):
    source_path = (DATA / 'backups' / name).resolve()
    if source_path.parent != (DATA / 'backups').resolve() or source_path.suffix != '.db' or not source_path.is_file():
        raise ValueError('Выберите существующую локальную резервную копию .db')
    with sqlite3.connect(source_path) as source:
        if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Резервная копия повреждена')
        if not source.execute("SELECT 1 FROM sqlite_master WHERE name='alembic_version'").fetchone():
            raise ValueError('Неизвестный формат резервной копии')
        if source.execute('SELECT version_num FROM alembic_version').fetchone()[0] not in ('0001','0002','0003'):
            raise ValueError('Версия резервной копии не поддерживается')
        backup()
        with sqlite3.connect(DATABASE) as target:
            source.backup(target)
            target.execute('DELETE FROM sessions')
            target.execute('DELETE FROM wb_accounts')
            target.execute("DELETE FROM app_settings WHERE key='password_hash'")
            target.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES('test_mode','true')")
            target.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES('safe_mode','true')")
            target.commit()


def setup():
    with Session() as db:
        if setting(db, 'password_hash'):
            print('Пароль уже установлен.')
            return
        password = getpass.getpass('Создайте пароль (минимум 12 символов): ')
        if len(password) < 12 or password != getpass.getpass('Повторите пароль: '):
            raise ValueError('Пароли не совпадают или слишком короткие')
        set_setting(db, 'password_hash', PasswordHasher().hash(password))
        db.commit()
        print('Пароль сохранён локально в виде Argon2-хеша.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['setup', 'backup', 'restore'])
    parser.add_argument('name', nargs='?')
    args = parser.parse_args()
    if args.command == 'setup':
        setup()
    elif args.command == 'backup':
        print(backup())
    else:
        if not args.name:
            raise SystemExit('Укажите имя файла резервной копии')
        restore(args.name)
        setup()
