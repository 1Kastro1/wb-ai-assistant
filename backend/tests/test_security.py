import asyncio
import json
import sqlite3
import pytest
import httpx
from app.security import EgressGuard, encrypt_secret, decrypt_secret, redact
from app.assistant import registry
from app.db import Session, Account, Activity, Audit, User, set_setting
from sqlalchemy import select
from app.config import DATA


def test_requires_login(client):
    client.cookies.clear()
    for url in ['/reviews','/security','/memory','/export/reviews','/products']:
        assert client.get(url).status_code == 401


def test_logout_invalidates_session(client):
    assert client.post('/auth/logout').status_code==200
    assert client.get('/auth/session').status_code==401


def test_first_run_password_setup():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import LoginSession, Setting
    with Session() as db:
        db.query(LoginSession).delete()
        db.query(Setting).filter(Setting.key=='password_hash').delete()
        db.commit()
    with TestClient(app,headers={'Origin':'http://127.0.0.1:3000'}) as fresh:
        assert fresh.get('/auth/status').json()=={'initialized':False}
        assert fresh.post('/auth/setup',json={'password':'different-password-123','confirmation':'wrong-password-456'}).status_code==400
        result=fresh.post('/auth/setup',json={'password':'different-password-123','confirmation':'different-password-123'})
        assert result.status_code==200
        fresh.headers['X-CSRF-Token']=result.json()['csrf']
        assert fresh.get('/auth/session').status_code==200
        assert fresh.post('/auth/setup',json={'password':'another-password-123','confirmation':'another-password-123'}).status_code==409


def test_csrf_and_origin(client):
    assert client.post('/backup',headers={'X-CSRF-Token':'wrong'}).status_code == 403
    assert client.get('/reviews',headers={'Origin':'https://evil.example'}).status_code == 403
    assert client.get('/health',headers={'Host':'evil.example'}).status_code == 400


def test_untrusted_remote_origin_is_blocked():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, headers={'Origin': 'https://attacker.example.com'}) as remote:
        assert remote.get('/auth/status').status_code == 403


def test_password_rate_limit(client):
    for _ in range(5):
        assert client.post('/auth/login',json={'password':'wrong'}).status_code == 401
    assert client.post('/auth/login',json={'password':'wrong'}).status_code == 429


def test_owner_can_create_colleague_with_separate_login(client):
    created = client.post('/users', json={'username':'anna','display_name':'Анна','password':'anna-password-123','position':'Менеджер Ozon'})
    assert created.status_code == 200
    assert created.json()['position'] == 'Менеджер Ozon'
    assert created.json()['must_change_password'] is True
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, headers={'Origin':'http://127.0.0.1:3000'}) as colleague:
        logged = colleague.post('/auth/login', json={'username':'anna','password':'anna-password-123'})
        assert logged.status_code == 200
        colleague.headers['X-CSRF-Token'] = logged.json()['csrf']
        assert colleague.get('/auth/session').json()['user']['role'] == 'member'
        assert colleague.get('/security').status_code == 403
        changed = colleague.post('/auth/password', json={'current_password':'anna-password-123','new_password':'anna-personal-password-456'})
        assert changed.status_code == 200
        logged = colleague.post('/auth/login', json={'username':'anna','password':'anna-personal-password-456'})
        colleague.headers['X-CSRF-Token'] = logged.json()['csrf']
        assert colleague.get('/security').status_code == 200
        assert colleague.get('/users').status_code == 403
        assert colleague.post('/settings/wb-token', json={'token':'member-cannot-replace-this'}).status_code == 403


def test_owner_can_change_position_and_invalid_position_is_rejected(client):
    user = client.post('/users', json={'username':'market','display_name':'Маркет','password':'market-password-123','position':'Менеджер WB'}).json()
    changed = client.patch('/users/'+user['id'], json={'position':'Менеджер Яндекс Маркет'})
    assert changed.status_code == 200
    assert changed.json()['position'] == 'Менеджер Яндекс Маркет'
    assert client.patch('/users/'+user['id'], json={'position':'Администратор'}).status_code == 422


def colleague_client(client, username, position):
    temporary = username + '-temporary-password-123'
    personal = username + '-personal-password-456'
    created = client.post('/users', json={'username':username,'display_name':username.title(),'password':temporary,'position':position})
    assert created.status_code == 200
    from fastapi.testclient import TestClient
    from app.main import app
    colleague = TestClient(app, headers={'Origin':'http://127.0.0.1:3000'})
    logged = colleague.post('/auth/login', json={'username':username,'password':temporary})
    colleague.headers['X-CSRF-Token'] = logged.json()['csrf']
    assert colleague.post('/auth/password', json={'current_password':temporary,'new_password':personal}).status_code == 200
    logged = colleague.post('/auth/login', json={'username':username,'password':personal})
    colleague.headers['X-CSRF-Token'] = logged.json()['csrf']
    return colleague


def test_marketplace_positions_have_real_wb_permissions(client):
    ozon = colleague_client(client, 'ozon-manager', 'Менеджер Ozon')
    try:
        session = ozon.get('/auth/session').json()['user']
        assert session['can_manage_wb'] is False
        assert ozon.get('/reviews').status_code == 200
        assert ozon.post('/jobs/sync/reviews').status_code == 403
        assert ozon.post('/auto-replies/start').status_code == 403
        assert ozon.patch('/settings/schedule', json={}).status_code == 403
        chat = ozon.post('/chat', json={'text':'подготовь ответ покупателю'})
        assert chat.status_code == 200
        assert 'менеджеру WB' in chat.json()['text']
    finally:
        ozon.close()

    wb = colleague_client(client, 'wb-manager', 'Менеджер WB')
    try:
        assert wb.get('/auth/session').json()['user']['can_manage_wb'] is True
        assert wb.post('/auto-replies/pause').status_code == 200
        assert wb.patch('/settings/schedule', json={}).status_code == 403
    finally:
        wb.close()


def test_owner_activity_log_records_actions_without_payloads(client):
    password = 'never-store-this-password-123'
    created = client.post('/users', json={'username':'audit-user','display_name':'Аудит','password':password,'position':'Менеджер WB'})
    assert created.status_code == 200
    activity = client.get('/team/activity')
    assert activity.status_code == 200
    rows = activity.json()
    assert any(row['path'] == '/users' and row['username'] == 'owner' for row in rows)
    assert password not in json.dumps(rows, ensure_ascii=False)
    with Session() as db:
        assert db.scalar(select(Activity).where(Activity.path == '/users')) is not None


def test_disabling_colleague_revokes_sessions(client):
    user = client.post('/users', json={'username':'pavel','display_name':'Павел','password':'pavel-password-123'}).json()
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, headers={'Origin':'http://127.0.0.1:3000'}) as colleague:
        logged = colleague.post('/auth/login', json={'username':'pavel','password':'pavel-password-123'})
        colleague.headers['X-CSRF-Token'] = logged.json()['csrf']
        assert client.patch('/users/'+user['id'], json={'enabled':False}).status_code == 200
        assert colleague.get('/security').status_code == 401


def test_token_not_exposed(client):
    token = 'SENSITIVE_WB_CREDENTIAL_123456789'
    response = client.post('/settings/wb-token',json={'token':token})
    assert response.status_code == 200
    assert token not in response.text + client.get('/security').text
    with Session() as db:
        stored = db.get(Account, 'owner').encrypted_token
        assert token not in stored
        assert decrypt_secret(stored) == token


def test_token_validation_not_echoed(client):
    r = client.post('/settings/wb-token',json={'token':'SECRET'})
    assert r.status_code == 422 and 'SECRET' not in r.text


def test_token_not_logged():
    result=redact('Authorization: secret-value token=abc password=def cookie=xyz')
    for value in ('secret-value','abc','def','xyz'):
        assert value not in result


def test_ai_cannot_read_secrets():
    for tool in ('read_any_file','shell','fetch_any_url','secrets.get','memory.save','reviews.publish'):
        with pytest.raises(ValueError): registry.authorize(tool)


@pytest.mark.parametrize('provider,operation,url', [('cloud','chat',None),('wb','upload',None),('wb','reviews','https://example.com'),('ollama','chat','http://127.0.0.1:11434/api/pull'),('wb','reviews','https://feedbacks-api.wildberries.ru.evil.com/api/v1/feedbacks')])
def test_egress_guard(provider,operation,url):
    with pytest.raises(ValueError): EgressGuard().validate(provider,operation,url)


def test_redirect_never_followed():
    seen=[]
    def handle(req):
        seen.append(str(req.url))
        return httpx.Response(302,headers={'location':'https://evil.example'})
    with pytest.raises(ValueError): asyncio.run(EgressGuard(httpx.MockTransport(handle)).request('wb','reviews',token='SECRET'))
    assert len(seen)==1
    with Session() as db:
        assert all('SECRET' not in str(a.__dict__) for a in db.scalars(select(Audit)))


def test_database_not_public(client):
    for path in ['/data/database/wb_assistant.db','/static/wb_assistant.db','/public/database.db','/openapi.json']:
        assert client.get(path).status_code == 404


def test_cloud_disabled_in_local_only(client):
    assert client.get('/security').json()['cloud_ai'] is False
    assert client.patch('/settings/modes',json={'safe_mode':False,'test_mode':False}).status_code == 400


def test_export_has_no_secrets(client,seeded):
    assert not {'encrypted_token','password_hash','csrf'} & set(client.get('/export/reviews').json()[0])


def test_backup_has_no_secrets(client,seeded):
    token='SPECIAL-CREDENTIAL-NOT-FOR-BACKUP'
    with Session() as db:
        db.add(Account(id='owner',encrypted_token=encrypt_secret(token)))
        db.commit()
    name=client.post('/backup').json()['file']
    with sqlite3.connect(DATA/'backups'/name) as backup:
        assert backup.execute('SELECT count(*) FROM wb_accounts').fetchone()[0]==0
        assert backup.execute('SELECT count(*) FROM sessions').fetchone()[0]==0
        assert not backup.execute("SELECT * FROM app_settings WHERE key='password_hash'").fetchall()
        assert backup.execute('SELECT count(*) FROM users').fetchone()[0]==0
        assert backup.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert backup.execute('SELECT count(*) FROM reviews').fetchone()[0]==4
