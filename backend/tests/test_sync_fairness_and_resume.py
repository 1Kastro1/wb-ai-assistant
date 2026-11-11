"""Imports get finite turns without cancelling work or losing page progress."""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from app.db import Session, Job, StoreProfile, Review
from app import integrations, ozon_reviews, queue_policy
from app.queue_policy import next_job, QueueYield


def test_all_old_syncs_receive_a_turn_during_continuous_three_store_work(client, monkeypatch):
    clock = [datetime.now(timezone.utc)]
    class Clock:
        @staticmethod
        def now(tz=None): return clock[0]
    monkeypatch.setattr(queue_policy, 'datetime', Clock)
    old = (clock[0] - timedelta(days=5)).isoformat()
    recent = (clock[0] - timedelta(minutes=6)).isoformat()
    sync_ids = []
    with Session() as db:
        for store in ('wb', 'ozon', 'yandex'):
            for kind in ('products', 'reviews'):
                jid = store + '-' + kind; sync_ids.append(jid)
                db.add(Job(id=jid, kind=kind, priority=200, created_at=old,
                    result={'account_id':store, 'last_served_at':old,
                            'checkpoint':{'processed':51100, 'last_id':'saved-cursor'}}))
            for suffix, kind, priority, flags in (
                    ('send', 'scheduled_publication', 180, {}),
                    ('regen', 'draft_recheck', 45, {'policy_regeneration':True}),
                    ('check', 'draft_recheck', 65, {'policy_scan':True, 'technical_scan':True})):
                db.add(Job(id=store+'-'+suffix, kind=kind, priority=priority,
                    created_at=recent, result={'last_served_at':recent, **flags}))
        db.commit()
        served = []
        # Model work can keep aging while the imports receive their slices.
        for _ in range(15):
            chosen = next_job(db); served.append(chosen.id)
            chosen.result = {**chosen.result, 'last_served_at':clock[0].isoformat()}
            db.commit(); clock[0] += timedelta(seconds=120)
        assert set(sync_ids) <= set(served)
        assert set(served[:6]) == set(sync_ids)
        for jid in sync_ids:
            assert db.get(Job,jid).result['checkpoint'] == {'processed':51100,'last_id':'saved-cursor'}


@pytest.mark.parametrize('kind', ['products','reviews','questions'])
def test_sync_aging_never_resumes_cancelled_or_delayed_jobs(client, kind):
    with Session() as db:
        db.add_all([
            Job(id='cancelled',kind=kind,created_at='2020-01-01',cancel_requested=True),
            Job(id='delayed',kind=kind,created_at='2020-01-01',result={'retry_at':time.time()+600}),
            Job(id='fresh',kind=kind,created_at=datetime.now(timezone.utc).isoformat()),
            Job(id='click',kind='single_draft',created_at=datetime.now(timezone.utc).isoformat())])
        db.commit()
        assert next_job(db).id == 'click'
        assert next_job(db,(kind,)).id == 'fresh'


def test_ozon_yields_after_a_review_and_resumes_opaque_page_without_repeating_comments(client, monkeypatch):
    with Session() as db:
        db.add(StoreProfile(id='ozon-page',provider='ozon',name='Ozon',config={'client_id':'1'}));db.commit()
    monkeypatch.setattr(integrations,'store_credentials',lambda db,store:(db.get(StoreProfile,store),'token'))
    pages, comments, checkpoints = [], [], []
    async def request(provider,operation,**kwargs):
        assert operation == 'reviews'
        cursor = kwargs['body'].get('last_id',''); pages.append(cursor)
        ids = ('one','two','three') if cursor == 'opaque-before' else ('four',)
        return {'reviews':[{'id':rid,'sku':123,'rating':5,'text':rid,'comments_amount':1} for rid in ids],
                'has_next':cursor == 'opaque-before', 'last_id':'opaque-after'}
    async def reply(headers,rid,cancelled=None):
        comments.append(rid); return {'state':'absent','text':''}
    monkeypatch.setattr(ozon_reviews.guard,'request',request)
    monkeypatch.setattr(ozon_reviews,'seller_reply',reply)
    def yield_checkpoint(state):
        checkpoints.append(state)
        if state['processed'] == 51102: raise QueueYield()
    with Session() as db:
        with pytest.raises(QueueYield):
            asyncio.run(ozon_reviews.sync_reviews(integrations.OzonProvider(),db,'ozon-page',
                resume={'last_id':'opaque-before','processed':51100},checkpoint=yield_checkpoint))
    assert checkpoints[-1] == {'last_id':'opaque-before','processed':51102,'page_done_ids':['one','two']}
    with Session() as db:
        assert asyncio.run(ozon_reviews.sync_reviews(integrations.OzonProvider(),db,'ozon-page',
            resume=checkpoints[-1],checkpoint=checkpoints.append)) == 51104
        assert len(list(db.scalars(select(Review.id).where(Review.wb_account_id=='ozon-page')))) == 4
    assert pages == ['opaque-before','opaque-before','opaque-after']
    assert comments == ['one','two','three','four']
    assert {'last_id':'opaque-after','processed':51103} in checkpoints


def test_ozon_cancel_during_partial_page_keeps_completed_cursor(client, monkeypatch):
    with Session() as db:
        db.add(StoreProfile(id='ozon-page',provider='ozon',name='Ozon',config={'client_id':'1'}));db.commit()
    monkeypatch.setattr(integrations,'store_credentials',lambda db,store:(db.get(StoreProfile,store),'token'))
    monkeypatch.setattr(ozon_reviews.guard,'request',AsyncMock(return_value={'reviews':[
        {'id':'one','sku':123,'rating':5,'comments_amount':0},
        {'id':'two','sku':123,'rating':5,'comments_amount':0}], 'has_next':False}))
    checkpoints=[]
    with Session() as db:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(ozon_reviews.sync_reviews(integrations.OzonProvider(),db,'ozon-page',
                cancelled=lambda:bool(checkpoints),checkpoint=checkpoints.append))
    assert checkpoints == [{'last_id':'','processed':1,'page_done_ids':['one']}]


def test_import_worker_yields_after_committed_slice_then_resumes(client, monkeypatch):
    from app import main
    clock=[0];resumed=[]
    monkeypatch.setattr(main,'time',SimpleNamespace(monotonic=lambda:clock[0],time=time.time))
    class Provider:
        async def sync_reviews(self,db,store,*args,resume=None,checkpoint=None,**kwargs):
            resumed.append(resume)
            if not resume:
                clock[0]=31
                checkpoint({'last_id':'opaque','processed':8,'page_done_ids':['one']})
                pytest.fail('An overdue import must yield after a committed checkpoint')
            assert resume=={'last_id':'opaque','processed':8,'page_done_ids':['one']}
            return 9
    monkeypatch.setattr(main,'provider_for',lambda *a:Provider())
    with Session() as db:
        db.add_all([Job(id='import',kind='reviews',result={'account_id':'owner'}),
                    Job(id='waiting',kind='draft_recheck',result={'account_id':'owner','policy_scan':True})]);db.commit()
    asyncio.run(main.execute_job('import'))
    with Session() as db:
        job=db.get(Job,'import')
        assert job.status=='queued' and not job.cancel_requested
        assert job.result['checkpoint']=={'last_id':'opaque','processed':8,'page_done_ids':['one']}
        assert db.get(Job,'waiting').status=='queued'
    asyncio.run(main.execute_job('import'))
    with Session() as db:
        assert db.get(Job,'import').status=='completed'
        assert db.get(Job,'import').result['processed']==9
    assert len(resumed)==2
