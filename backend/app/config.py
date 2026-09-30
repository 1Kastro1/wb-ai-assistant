import os
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
ORIGIN = 'http://127.0.0.1:3000'
PUBLIC_ORIGIN = os.environ.get('WB_PUBLIC_ORIGIN', '').strip().rstrip('/')
ORIGINS = [ORIGIN] + ([PUBLIC_ORIGIN] if PUBLIC_ORIGIN and PUBLIC_ORIGIN != ORIGIN else [])
MODEL = os.environ.get('OLLAMA_MODEL', 'qwen3:4b')
REAL_PUBLISH = os.environ.get('WB_ENABLE_REAL_PUBLISH') == 'I_EXPLICITLY_AUTHORIZE_REAL_PUBLISH'
