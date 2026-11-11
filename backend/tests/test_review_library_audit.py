import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select,func
from app.db import (Session,StoreProfile,Product,Review,Draft,Job,Memory,Setting,
    VerifiedReplyText,VerifiedReplyContext,ReviewLibraryAuditRow,setting,set_setting)
from app.review_library_audit import start,step,report
from app.verified_reply_library import remember,variants,migrate_legacy,text_id
from app.owner_reply_policy import fingerprint


def seed(db,*,published=False,manual=False):
    for market in ('ozon','wb','yandex'):
        db.add(StoreProfile(id=market,name=market,provider=market))
        db.add(Product(id='p-'+market,name='Товар',marketplace=market))
        db.flush()
        db.add(Review(id='r-'+market,wb_review_id=market,product_id='p-'+market,wb_account_id=market,
            marketplace=market,rating=5,text='Отлично!',is_answered=published,
            existing_answer='Добрый день! Спасибо за отзыв.' if published else '',manual=manual))
        db.flush()
        db.add(Draft(id='d-'+market,review_id='r-'+market,text='Добрый день! Спасибо за отзыв.',
            original='Original employee text',edits=[{'by':'employee'}],revision=7))
    db.commit()


async def good(answer,review,product,rules,facts,assess,**kwargs):
    return answer,{'checked':True,'passed':True,'issues':[],'fingerprint':fingerprint(rules)}


def test_durable_dedup_preserves_exact_verified_presentation_and_expired_legacy_is_inactive():
    with Session() as db:
        a='Добрый день!\nСпасибо за отзыв.';b='  добрый день! Спасибо за отзыв. '
        tid=remember(db,'a','exact',a,{'passed':True},marketplace='wb',verified_at=time.time()-86400*90)
        assert remember(db,'b','exact',b,{'passed':True},marketplace='yandex')==tid
        remember(db,'c','legacy',a,{'passed':True},verified_at=1)
        assert variants(db,'a','exact')[0]['answer']==a
        assert variants(db,'b','exact')[0]['answer']==b
        assert variants(db,'c','exact')==[]
        assert db.scalar(select(func.count()).select_from(VerifiedReplyText))==1
        for i in range(1100):remember(db,str(i),'exact',a,{'passed':True})
        db.commit()
    with Session() as db:
        assert variants(db,'0','exact') and variants(db,'1099','exact')
        assert db.scalar(select(func.count()).select_from(VerifiedReplyText))==1


def test_legacy_conversion_does_not_turn_expired_proof_into_new_approval():
    with Session() as db:
        db.add(Setting(key='verified-replies:a',value={'expired':{'answer':'Спасибо!','verdict':{'passed':True},'at':1},
            'recent':{'answer':'Спасибо!','verdict':{'passed':False},'at':time.time()}}));db.flush()
        result=migrate_legacy(db);db.commit()
        assert result['unique_templates']==1 and result['contexts']==2
        assert variants(db,'expired','exact')==[]
        assert variants(db,'recent','exact')[0]['verdict']['passed'] is False
        assert not db.get(Setting,'verified-replies:a')
        assert migrate_legacy(db)['converted_approvals']==0


def test_activation_pauses_all_automation_preserves_old_job_progress_and_orders_stores():
    with Session() as db:
        seed(db)
        db.add(Job(id='old',kind='reviews',status='queued',result={'cursor':'kept'}))
        set_setting(db,'automation_schedule',{'enabled':True,'publication_enabled':True,'drafts_enabled':True,'reviews_sync_enabled':True})
        jid=start(db).id;db.commit()
        state=db.get(Job,jid).result
        assert [s['marketplace'] for s in state['stages']]==['yandex','wb','ozon']
        assert state['total']==3 and setting(db,'safe_mode') is True
        assert setting(db,'review_library_maintenance') is True
        assert not any(setting(db,'automation_schedule')[f] for f in ('enabled','publication_enabled','drafts_enabled','reviews_sync_enabled'))
        assert db.get(Job,'old').status=='cancelled' and db.get(Job,'old').result['cursor']=='kept'
        assert start(db).id==jid
        from app.queue_policy import next_job
        db.add(Job(id='interactive',kind='single_draft',priority=0));db.flush()
        assert next_job(db).id==jid


@pytest.mark.parametrize('published',[False,True])
def test_sequential_audit_preserves_answers_and_saves_one_body_across_three_markets(monkeypatch,published):
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules',good)
    with Session() as db:seed(db,published=published);jid=start(db).id;db.commit()
    asyncio.run(step(jid,limit=1))
    with Session() as db:
        assert db.get(Job,jid).result['stages'][0]['checked']==1
        assert db.get(Job,jid).result['stages'][1]['checked']==0
    for _ in range(7):asyncio.run(step(jid,limit=1))
    with Session() as db:
        result=report(db)
        assert result['job']['status']=='completed' and result['job']['verified']==3
        assert result['library']['unique_templates']==1
        assert set(result['library']['markets'])=={'wb','ozon','yandex'}
        assert setting(db,'safe_mode') is True
        assert not setting(db,'automation_schedule')['publication_enabled']
        for d in db.scalars(select(Draft)):
            assert d.revision==7 and d.original=='Original employee text' and d.edits==[{'by':'employee'}]
            assert d.text=='Добрый день! Спасибо за отзыв.'
            if not published:assert d.quality['passed'] is True
            else:assert d.quality=={}
        assert db.scalar(select(func.count()).select_from(ReviewLibraryAuditRow))==3


def test_retry_does_not_advance_market_or_approve_failed_check(monkeypatch):
    async def timeout(answer,*args,**kwargs):
        return answer,{'passed':False,'checked':False,'technical_error':True,'retryable':True,'issues':['timeout']}
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules',timeout)
    with Session() as db:seed(db);jid=start(db).id;db.commit()
    asyncio.run(step(jid))
    with Session() as db:
        j=db.get(Job,jid)
        assert j.status=='queued' and j.result['stage']==0 and j.result['checked']==0
        assert j.result['retry_at']>time.time()
        assert not list(db.scalars(select(VerifiedReplyContext)))
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules',good)
    asyncio.run(step(jid))
    with Session() as db:
        assert db.get(Job,jid).status=='completed'
        assert db.get(ReviewLibraryAuditRow,(jid,'r-yandex')).attempts==2


def test_changed_rules_stop_campaign_and_manual_approved_answer_is_reference_only(monkeypatch):
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules',good)
    with Session() as db:seed(db,manual=True);jid=start(db).id;db.commit()
    asyncio.run(step(jid,limit=1))
    with Session() as db:
        rows=list(db.scalars(select(VerifiedReplyContext)))
        assert all(row.kind=='reference' for row in rows)
        assert not db.get(Draft,'d-yandex').quality['passed']
        db.add(Memory(id='new',kind='REVIEW_RULE',text='Новое правило',enabled=True));db.commit()
    asyncio.run(step(jid))
    with Session() as db:
        assert db.get(Job,jid).status=='failed'
        assert db.get(Job,jid).result['stages'][1]['checked']==0


def test_review_changed_during_check_cannot_receive_old_approval(monkeypatch):
    async def mutate(answer,*args,**kwargs):
        with Session() as db:
            db.get(Draft,'d-yandex').text='Ручное изменение';db.commit()
        return await good(answer,*args,**kwargs)
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules',mutate)
    with Session() as db:seed(db);jid=start(db).id;db.commit()
    asyncio.run(step(jid,limit=1))
    with Session() as db:
        assert db.get(Draft,'d-yandex').text=='Ручное изменение'
        assert not list(db.scalars(select(VerifiedReplyContext)))
        assert db.get(Job,jid).result['checked']==0


def test_contradictory_technical_success_never_teaches_template(monkeypatch):
    async def broken(answer,*args,**kwargs):
        return answer,{'passed':True,'checked':True,'technical_error':True,'issues':[]}
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules',broken)
    with Session() as db:seed(db);jid=start(db).id;db.commit()
    asyncio.run(step(jid,limit=1))
    with Session() as db:
        assert not list(db.scalars(select(VerifiedReplyContext)))
        assert db.get(Job,jid).result['checked']==0


def test_status_is_owner_only_and_scheduler_cannot_restart_during_audit(client,monkeypatch):
    from app.main import queue_scheduled_jobs
    with Session() as db:seed(db);jid=start(db).id;db.commit()
    queue_scheduled_jobs()
    response=client.get('/review-library/status')
    assert response.status_code==200 and response.json()['job']['id']==jid
    with Session() as db:
        assert len(list(db.scalars(select(Job).where(Job.status=='queued'))))==1
    client.post('/auth/logout')
    assert client.get('/review-library/status').status_code==401


@pytest.mark.parametrize('status',['uncertain','answer_pending','published','mismatch'])
def test_pending_or_confirmed_publication_never_becomes_sendable_draft(monkeypatch,status):
    from app.db import Publication
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules',good)
    with Session() as db:
        seed(db)
        db.get(Review,'r-yandex').status='manual_review'
        db.add(Publication(id='sent',review_id='r-yandex',action_id='old',status=status))
        jid=start(db).id;db.commit()
    asyncio.run(step(jid,limit=1))
    with Session() as db:
        assert db.get(Review,'r-yandex').status=='manual_review'
        assert db.get(Publication,'sent').status==status
        assert not list(db.scalars(select(VerifiedReplyContext)))
        assert db.get(Job,jid).result['attention']==1


def test_published_status_is_read_only_even_when_provider_flag_is_stale(monkeypatch):
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules',good)
    with Session() as db:
        seed(db)
        review=db.get(Review,'r-yandex');review.status='published'
        review.existing_answer='Добрый день! Спасибо за отзыв.'
        jid=start(db).id;db.commit()
    asyncio.run(step(jid,limit=1))
    with Session() as db:
        assert db.get(Review,'r-yandex').status=='published'
        assert db.get(Draft,'d-yandex').quality=={}
        assert db.get(Job,jid).result['verified']==1


def test_empty_store_checkpoint_remains_queued_after_worker_slice():
    with Session() as db:
        seed(db)
        db.add(StoreProfile(id='000-empty',provider='yandex',name='Empty'))
        job=start(db);jid=job.id;job.status='running';db.commit()
    asyncio.run(step(jid,limit=1))
    with Session() as db:
        job=db.get(Job,jid)
        assert job.status=='queued' and job.result['stage']==1
        assert job.result['checked']==0
