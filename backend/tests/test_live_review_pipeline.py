import asyncio
import time
from datetime import datetime,timezone,timedelta
from sqlalchemy import select
from app.db import Session,Product,Review,Draft,Job,StoreProfile,Publication


def test_new_review_schedule_is_independent_from_running_archive():
    from app.automation_schedule import enqueue_generation
    with Session() as db:
        store=StoreProfile(id='shop',provider='wb',name='Shop');db.add(store)
        db.add(Job(id='archive',kind='drafts',status='running',result={'account_id':'shop','scheduled':True,'archive':True,'processed':40}))
        schedule={'enabled':True,'drafts_enabled':True,'drafts_all_history':True,'drafts_hours':2}
        assert enqueue_generation(db,schedule,store,'drafts',time.time())
        db.commit()
        jobs=list(db.scalars(select(Job).where(Job.kind=='drafts')))
        assert len(jobs)==2
        fresh=next(j for j in jobs if j.id!='archive')
        assert fresh.result['fresh_reviews'] and fresh.result['since'] and fresh.result['received_since']
        assert db.get(Job,'archive').result['processed']==40
        assert not enqueue_generation(db,schedule,store,'drafts',time.time()+7200)


def test_newly_received_old_review_is_generated_and_receipt_survives_sync():
    from app.integrations import upsert_market_review
    from app.batch_processing import run_draft_batch
    with Session() as db:
        store=StoreProfile(id='shop',provider='yandex',name='Shop');db.add(store);db.commit()
        review=upsert_market_review(db,store,'r','p','Product','',5,'Отлично!','2020-01-01T00:00:00+00:00')
        db.commit();received=review.received_at
        upsert_market_review(db,store,'r','p','Product','',5,'Отлично!','2020-01-01T00:00:00+00:00');db.commit()
        assert review.received_at==received and received
        db.add(Job(id='fresh',kind='drafts',result={'account_id':'shop','scheduled':True,
            'since':'2026-01-01','received_since':received,'fresh_reviews':True}));db.commit()
    seen=[]
    async def generate(db,rid,*args,**kwargs):
        seen.append(rid)
        db.add(Draft(id=rid,review_id=rid,text='Спасибо!',original='Спасибо!',quality={'passed':True}));db.commit()
        return db.get(Draft,rid)
    async def between():pass
    asyncio.run(run_draft_batch('fresh',generate,between))
    assert seen==['shop:r']


def test_delivery_metrics_only_use_real_receipt_and_confirmation():
    from app.pipeline_metrics import delivery_latency
    from app.pipeline_overview import _overview
    current=datetime.now(timezone.utc)
    with Session() as db:
        store=StoreProfile(id='shop',provider='wb',name='Shop');db.add(store)
        db.add(Product(id='p',name='Product'));db.flush()
        for i,delay in enumerate((10,30,60,100,1000)):
            rid=str(i)
            db.add(Review(id=rid,wb_review_id=rid,wb_account_id='shop',product_id='p',rating=5,text='Good',
                received_at=(current-timedelta(seconds=delay)).isoformat()))
            db.flush();db.add(Publication(id=rid,review_id=rid,action_id='',status='published',confirmed_at=current.isoformat()))
        db.add(Review(id='old',wb_review_id='old',wb_account_id='shop',product_id='p',rating=5,text='Good',received_at=''))
        db.flush();db.add(Publication(id='old',review_id='old',action_id='',status='published',confirmed_at=''))
        db.commit()
        result=delivery_latency(db,'shop')
        assert result=={'samples':5,'p50_seconds':60.0,'p95_seconds':1000.0}
        counts=_overview(db,{'shop':store})[0]['counts']
        assert counts['published_today']==5 and counts['published']==6
