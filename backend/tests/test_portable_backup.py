import sqlite3
from sqlalchemy import delete, select, func

from app.db import Session, Review


PASSWORD='portable-backup-password-123'


def test_encrypted_portable_export_and_restore(client,seeded,tmp_path):
    plain=client.post('/backup').json()['file']
    from app.config import DATA
    with sqlite3.connect(DATA/'backups'/plain) as check:
        assert check.execute('SELECT count(*) FROM sessions').fetchone()[0]==0
    exported=client.post('/portable/export',json={'password':PASSWORD})
    assert exported.status_code==200
    assert exported.content.startswith(b'WBAIPORT1')
    assert exported.headers['x-backup-encrypted']=='AES-256-GCM'
    assert exported.headers['x-secrets-included']=='false'
    assert b'owner-hash' not in exported.content
    encrypted=tmp_path/'export.wbai';decrypted=tmp_path/'export.db';encrypted.write_bytes(exported.content)
    from app.maintenance import _decrypt_portable
    _decrypt_portable(encrypted,decrypted,PASSWORD)
    with sqlite3.connect(decrypted) as check:
        assert check.execute('SELECT count(*) FROM wb_accounts').fetchone()[0]==0
        assert check.execute('SELECT count(*) FROM sessions').fetchone()[0]==0
        assert check.execute("SELECT count(*) FROM app_settings WHERE key='password_hash'").fetchone()[0]==0

    with Session() as db:
        db.execute(delete(Review))
        db.commit()
        assert db.scalar(select(func.count()).select_from(Review))==0

    restored=client.post('/portable/restore',data={'password':PASSWORD},files={'file':('shop.wbai',exported.content,'application/octet-stream')})
    assert restored.status_code==200
    assert restored.json()['secrets_restored'] is False
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Review))==4

    # The current computer keeps its own login session after data restoration.
    assert client.get('/reviews').status_code==200


def test_portable_restore_rejects_wrong_password(client,seeded):
    exported=client.post('/portable/export',json={'password':PASSWORD})
    restored=client.post('/portable/restore',data={'password':'wrong-password-123'},files={'file':('shop.wbai',exported.content,'application/octet-stream')})
    assert restored.status_code==400
    assert 'Неверный пароль' in restored.json()['detail']
