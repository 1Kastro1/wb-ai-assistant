import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


def data_root() -> Path:
    path = Path(os.environ.get('WB_DATA_DIR', str(Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local/share')) / 'WBAIAssistant' / 'data'))).resolve()
    if any(x.lower() in {'public', 'static', 'onedrive', 'dropbox', 'google drive'} or x.lower().startswith('onedrive -') for x in path.parts):
        raise RuntimeError('Данные нельзя хранить в публичной или синхронизируемой папке')
    return path


DATA = data_root()
for folder in ('database', 'backups', 'catalogs', 'uploads', 'exports', 'temp', 'models'):
    (DATA / folder).mkdir(parents=True, exist_ok=True)
DATABASE = DATA / 'database/wb_assistant.db'
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip() or ('sqlite:///' + str(DATABASE).replace('\\', '/'))


def repair_sqlite_sidecars():
    """Recover from a corrupt/stale WAL only when the main database is proven sound."""
    if not DATABASE.exists() or not DATABASE_URL.startswith('sqlite:'):
        return {'status': 'not_needed'}
    try:
        with sqlite3.connect(DATABASE, timeout=5) as db:
            result = db.execute('PRAGMA quick_check').fetchone()[0]
        if result == 'ok':
            return {'status': 'ok'}
    except sqlite3.DatabaseError as original:
        result = str(original)
    uri = 'file:' + DATABASE.as_posix() + '?immutable=1'
    try:
        with sqlite3.connect(uri, uri=True) as main:
            if main.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                return {'status': 'failed', 'detail': result}
    except sqlite3.DatabaseError:
        return {'status': 'failed', 'detail': result}
    folder = DATA / 'recovery-snapshots' / ('sidecar-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DATABASE, folder / DATABASE.name)
    for suffix in ('-wal', '-shm'):
        sidecar = Path(str(DATABASE) + suffix)
        if sidecar.exists():
            shutil.move(str(sidecar), str(folder / sidecar.name))
    return {'status': 'recovered', 'snapshot': str(folder)}


SQLITE_STARTUP_HEALTH = repair_sqlite_sidecars()
ORIGIN = 'http://127.0.0.1:3000'
PUBLIC_ORIGIN = os.environ.get('WB_PUBLIC_ORIGIN', '').strip().rstrip('/')
ORIGINS = [ORIGIN] + ([PUBLIC_ORIGIN] if PUBLIC_ORIGIN and PUBLIC_ORIGIN != ORIGIN else [])
MODEL = os.environ.get('OLLAMA_MODEL', 'qwen3:4b')
REAL_PUBLISH = os.environ.get('WB_ENABLE_REAL_PUBLISH') == 'I_EXPLICITLY_AUTHORIZE_REAL_PUBLISH'
