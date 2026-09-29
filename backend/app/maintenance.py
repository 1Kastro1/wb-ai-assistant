import argparse
import getpass
import sqlite3
import os
import secrets
from contextlib import closing
from datetime import datetime
from pathlib import Path
from argon2 import PasswordHasher
from argon2.low_level import hash_secret_raw, Type
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.exceptions import InvalidTag
from .config import DATA, DATABASE
from .db import Session, Setting, setting, set_setting


PORTABLE_MAGIC = b'WBAIPORT1'
PORTABLE_HEADER = len(PORTABLE_MAGIC) + 16 + 12


def _portable_key(password, salt):
    if len(password) < 12:
        raise ValueError('Пароль копии должен содержать не менее 12 символов')
    return hash_secret_raw(password.encode(), salt, time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, type=Type.ID)


def portable_backup(password):
    source_name = backup()
    source = DATA / 'backups' / source_name
    # Re-apply the allowlist immediately before encryption. This also protects
    # against a future change to the ordinary local-backup implementation.
    with closing(sqlite3.connect(source)) as sanitized:
        sanitized.execute('PRAGMA journal_mode=DELETE')
        sanitized.execute('DELETE FROM wb_accounts')
        sanitized.execute('DELETE FROM sessions')
        sanitized.execute("DELETE FROM app_settings WHERE key='password_hash'")
        sanitized.commit()
        sanitized.execute('VACUUM')
    _check_backup_database(source)
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    header = PORTABLE_MAGIC + salt + nonce
    destination = DATA / 'exports' / (datetime.now().strftime('wb-assistant-%Y%m%d-%H%M%S-%f') + '.wbai')
    encryptor = Cipher(algorithms.AES(_portable_key(password, salt)), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    with source.open('rb') as incoming, destination.open('wb') as outgoing:
        outgoing.write(header)
        while chunk := incoming.read(1024 * 1024):
            outgoing.write(encryptor.update(chunk))
        outgoing.write(encryptor.finalize())
        outgoing.write(encryptor.tag)
    return destination


def _decrypt_portable(source, destination, password):
    size = source.stat().st_size
    if size <= PORTABLE_HEADER + 16:
        raise ValueError('Файл резервной копии повреждён')
    with source.open('rb') as incoming:
        header = incoming.read(PORTABLE_HEADER)
        if not header.startswith(PORTABLE_MAGIC):
            raise ValueError('Неизвестный формат резервной копии')
        salt = header[len(PORTABLE_MAGIC):len(PORTABLE_MAGIC)+16]
        nonce = header[-12:]
        incoming.seek(-16, os.SEEK_END)
        tag = incoming.read(16)
        incoming.seek(PORTABLE_HEADER)
        remaining = size - PORTABLE_HEADER - 16
        decryptor = Cipher(algorithms.AES(_portable_key(password, salt)), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(header)
        try:
            with destination.open('wb') as outgoing:
                while remaining:
                    chunk = incoming.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError('Файл резервной копии повреждён')
                    remaining -= len(chunk)
                    outgoing.write(decryptor.update(chunk))
                outgoing.write(decryptor.finalize())
        except InvalidTag:
            destination.unlink(missing_ok=True)
            raise ValueError('Неверный пароль или повреждённая резервная копия') from None


def _check_backup_database(path):
    with closing(sqlite3.connect(path)) as source:
        if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Резервная копия повреждена')
        version = source.execute("SELECT version_num FROM alembic_version").fetchone() if source.execute("SELECT 1 FROM sqlite_master WHERE name='alembic_version'").fetchone() else None
        if not version or version[0] not in ('0001','0002','0003'):
            raise ValueError('Версия резервной копии не поддерживается')
        if source.execute('SELECT count(*) FROM wb_accounts').fetchone()[0] or source.execute('SELECT count(*) FROM sessions').fetchone()[0] or source.execute("SELECT count(*) FROM app_settings WHERE key='password_hash'").fetchone()[0]:
            raise ValueError('Копия содержит запрещённые секреты')


def portable_restore(source_path, password):
    temp_path = DATA / 'temp' / f'portable-{secrets.token_hex(12)}.db'
    try:
        _decrypt_portable(Path(source_path), temp_path, password)
        _check_backup_database(temp_path)
        backup()
        with closing(sqlite3.connect(DATABASE)) as target:
            password_row = target.execute("SELECT value FROM app_settings WHERE key='password_hash'").fetchone()
            sessions = target.execute('SELECT id,expires,csrf FROM sessions').fetchall()
        with closing(sqlite3.connect(temp_path)) as source, closing(sqlite3.connect(DATABASE)) as target:
            source.backup(target)
            if password_row:
                target.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES('password_hash',?)", password_row)
            target.executemany('INSERT OR REPLACE INTO sessions(id,expires,csrf) VALUES(?,?,?)', sessions)
            target.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES('test_mode','true')")
            target.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES('safe_mode','true')")
            target.commit()
        return {'ok': True, 'secrets_restored': False, 'safe_mode': True, 'test_mode': True}
    finally:
        temp_path.unlink(missing_ok=True)


def backup():
    destination = DATA / 'backups' / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.db')
    with closing(sqlite3.connect(DATABASE)) as source, closing(sqlite3.connect(destination)) as target:
        source.backup(target)
        # Make the .db file self-contained before deleting secrets. Otherwise
        # those DELETEs can remain only in a sidecar WAL that is not exported.
        target.execute('PRAGMA journal_mode=DELETE')
        target.execute('PRAGMA secure_delete=ON')
        # Exclude credentials and browser sessions from portable backups.
        target.execute('DELETE FROM wb_accounts')
        target.execute('DELETE FROM sessions')
        target.execute("DELETE FROM app_settings WHERE key='password_hash'")
        target.commit()
        target.execute('VACUUM')
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Проверка резервной копии не пройдена')
    backups = sorted((DATA / 'backups').glob('*.db'), key=lambda path: path.stat().st_mtime, reverse=True)
    for obsolete in backups[10:]:
        obsolete.unlink(missing_ok=True)
    return destination.name


def restore(name):
    source_path = (DATA / 'backups' / name).resolve()
    if source_path.parent != (DATA / 'backups').resolve() or source_path.suffix != '.db' or not source_path.is_file():
        raise ValueError('Выберите существующую локальную резервную копию .db')
    with closing(sqlite3.connect(source_path)) as source:
        if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Резервная копия повреждена')
        if not source.execute("SELECT 1 FROM sqlite_master WHERE name='alembic_version'").fetchone():
            raise ValueError('Неизвестный формат резервной копии')
        if source.execute('SELECT version_num FROM alembic_version').fetchone()[0] not in ('0001','0002','0003'):
            raise ValueError('Версия резервной копии не поддерживается')
        backup()
        with closing(sqlite3.connect(DATABASE)) as target:
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
