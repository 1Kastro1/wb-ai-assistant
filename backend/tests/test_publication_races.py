import httpx
import pytest
from concurrent.futures import ThreadPoolExecutor
from app.integrations import guard
from app import config, services
from app.db import Session, Account, set_setting, Publication, Review
from app.security import encrypt_secret
from sqlalchemy import select


def prepare(client,monkeypatch,handler):
    monkeypatch.setattr(config,'REAL_PUBLISH',True)
    monkeypatch.setattr(services,'REAL_PUBLISH',True)
    monkeypatch.setattr(guard,'transport',httpx.MockTransport(handler))
    with Session() as db:
        db.add(Account(id='owner',encrypted_token=encrypt_secret('TEST-ONLY-NO-LIVE-TOKEN')))
        set_setting(db,'test_mode',False)
        db.commit()
    assert client.post('/reviews/r3/draft',json={}).status_code==200
    return client.post('/actions/publish',json={'review_ids':['r3']}).json()['id']


def test_concurrent_confirm_only_one_mock_post(client,seeded,monkeypatch):
    posted=[]
    def handler(req):
        if req.method=='GET':return httpx.Response(200,json={'data':{'id':'r3','answer':None}})
        posted.append(req.content)
        return httpx.Response(204)
    aid=prepare(client,monkeypatch,handler)
    def confirm():return client.post('/actions/'+aid+'/confirm',json={}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses=list(pool.map(lambda _:confirm(),range(2)))
    assert sorted(statuses)==[200,400]
    assert len(posted)==1


def test_ambiguous_timeout_never_retries_post(client,seeded,monkeypatch):
    posted=[]
    def handler(req):
        if req.method=='GET':return httpx.Response(200,json={'data':{'id':'r3','answer':None}})
        posted.append(req.content)
        raise httpx.ReadTimeout('simulated timeout')
    aid=prepare(client,monkeypatch,handler)
    result=client.post('/actions/'+aid+'/confirm',json={}).json()
    assert result['status']=='needs_review'
    assert len(posted)==1
    another=client.post('/actions/publish',json={'review_ids':['r3']}).json()['id']
    assert client.post('/actions/'+another+'/confirm',json={}).status_code==400
    assert len(posted)==1


def test_wrong_remote_id_blocks_post(client,seeded,monkeypatch):
    posted=[]
    def handler(req):
        if req.method=='GET':return httpx.Response(200,json={'data':{'id':'wrong','answer':None}})
        posted.append(req.content)
        return httpx.Response(204)
    aid=prepare(client,monkeypatch,handler)
    assert client.post('/actions/'+aid+'/confirm',json={}).json()['status']=='needs_review'
    assert posted==[]
