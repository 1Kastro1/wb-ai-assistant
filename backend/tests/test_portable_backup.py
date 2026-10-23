import sqlite3
from sqlalchemy import delete, select, func

from app.db import Session, Review, Job


PASSWORD='portable-backup-password-123'


def test_current_backup_excludes_enrollment_secrets_and_restore_keeps_local_requests(client,seeded,tmp_path):
    from app.db import RegistrationRequest,TeamInvite,GenerationClaim,GenerationOutcome
    from app.maintenance import _decrypt_portable,_check_backup_database
    import pytest
    with Session() as db:
        db.add(RegistrationRequest(id='pending-local',username='pending.local',password_hash='private-registration-hash',receipt_hash='private-receipt'))
        db.add(TeamInvite(token_hash='private-invite',position='operator',store_ids=['owner'],created_by='owner',expires=9999999999))
        db.add(GenerationClaim(review_id='r1', token='old-worker', expires_at=9999999999))
        db.add(GenerationOutcome(job_id='restored-job', review_id='r1', attention=True))
        db.commit()
    exported=client.post('/portable/export',json={'password':PASSWORD})
    assert exported.status_code==200,exported.text
    encrypted=tmp_path/'enrollment.wbai';decrypted=tmp_path/'enrollment.db'
    encrypted.write_bytes(exported.content);_decrypt_portable(encrypted,decrypted,PASSWORD)
    _check_backup_database(decrypted)
    with sqlite3.connect(decrypted) as check:
        assert check.execute('SELECT version_num FROM alembic_version').fetchone()[0]=='0013'
        assert check.execute('SELECT count(*) FROM generation_claims').fetchone()[0]==0
        assert check.execute('SELECT count(*) FROM generation_outcomes').fetchone()[0]==1
        for table in ('users','sessions','registration_requests','team_invites'):
            assert check.execute('SELECT count(*) FROM '+table).fetchone()[0]==0
    restored=client.post('/portable/restore',data={'password':PASSWORD},files={'file':('shop.wbai',exported.content,'application/octet-stream')})
    assert restored.status_code==200,restored.text
    with Session() as db:
        assert db.get(RegistrationRequest,'pending-local').username=='pending.local'
        assert db.get(TeamInvite,'private-invite').created_by=='owner'
        assert db.get(GenerationClaim, 'r1') is None
        assert db.get(GenerationOutcome, ('restored-job', 'r1')).attention
    with sqlite3.connect(decrypted) as check:
        check.execute("INSERT INTO registration_requests(id,username,password_hash,receipt_hash,status,created_at,reviewed_at,reviewed_by) VALUES('leak','leak','secret','receipt','pending','test','','')")
        check.commit()
    with pytest.raises(ValueError,match='секреты'):_check_backup_database(decrypted)


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


def test_portable_restore_waits_for_active_jobs(client,seeded):
    exported=client.post('/portable/export',json={'password':PASSWORD})
    with Session() as db:
        db.add(Job(id='active-during-restore',kind='reviews',status='queued'))
        db.commit()
    restored=client.post('/portable/restore',data={'password':PASSWORD},files={'file':('shop.wbai',exported.content,'application/octet-stream')})
    assert restored.status_code==400
    assert 'очереди' in restored.json()['detail']


def test_encrypted_folder_sync(client,seeded,tmp_path):
    configured=client.patch('/settings/portable-sync',json={'enabled':True,'directory':str(tmp_path),'password':PASSWORD,'hours':24})
    assert configured.status_code==200, configured.text
    assert configured.json()['configured'] is True
    synced=client.post('/portable/sync-now')
    assert synced.status_code==200
    destination=tmp_path/synced.json()['file']
    assert destination.read_bytes().startswith(b'WBAIPORT1')
    verified=client.post('/portable/verify')
    assert verified.status_code==200
    assert verified.json()['ok'] is True
    assert verified.json()['reviews']==4
    assert verified.json()['file']==destination.name


def test_folder_sync_detects_corrupt_backup_without_touching_database(client,seeded,tmp_path):
    client.patch('/settings/portable-sync',json={'enabled':True,'directory':str(tmp_path),'password':PASSWORD,'hours':24})
    synced=client.post('/portable/sync-now')
    destination=tmp_path/synced.json()['file']
    data=bytearray(destination.read_bytes());data[-20]^=1;destination.write_bytes(data)
    verified=client.post('/portable/verify')
    assert verified.status_code==400
    assert 'повреждённая' in verified.json()['detail']
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Review))==4
