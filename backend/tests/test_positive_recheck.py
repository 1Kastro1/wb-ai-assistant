import asyncio
from unittest.mock import AsyncMock
from sqlalchemy import select
from app.db import Session, User, Product, Review, Draft, Memory, Job, Publication, Action
from app import rule_execution as execution, positive_recheck as repair


def seed():
    with Session() as db:
        db.add(User(id='owner-user', username='owner-recheck', display_name='Owner', role='owner', enabled=True, password_hash='unused'))
        db.add(Product(id='p', name='Лампочки', brand='Brand')); db.flush()
        rule='Пиши кратко и доброжелательно, не выдумывай свойства товара.'
        db.add(Memory(id='rule', kind='REVIEW_RULE', text=rule))
        for rid, text in [('blank',''), ('praise','Отличные лампочки.'), ('human','Хорошая штука'), ('complaint','Все супер, но товар сломан'), ('published',''), ('low','')]:
            db.add(Review(id=rid, wb_review_id=rid, product_id='p', text=text, rating=3 if rid=='low' else 5,
                created_at='2026-11-01', status='manual_review'))
            db.flush()
            db.add(Draft(id='d-'+rid, review_id=rid, text='Добрый день! Сохранённый ответ.', original='Добрый день! Сохранённый ответ.',
                revision=7, edits=[{'by':'employee','old':'x','new':'y'}] if rid=='human' else [],
                quality={'passed':False,'text_issues':[], 'owner_rules':{'checked':False,'passed':False,'technical_error':True}}))
        db.add(Publication(id='pub', review_id='published', action_id='old', status='uncertain')); db.commit()
    return rule


def test_local_positive_recheck_preserves_edits_publications_and_defers_complaints(client,monkeypatch):
    from app.integrations import ollama
    rule=seed()
    monkeypatch.setattr(execution,'PROFILES',{**execution.PROFILES,execution.digest(rule):{'local_positive':True}})
    judge=AsyncMock(side_effect=AssertionError('Local recheck must never invoke model'))
    monkeypatch.setattr(ollama,'structured',judge);monkeypatch.setattr(ollama,'chat',judge)
    with Session() as db:
        job=repair.enqueue(db,'owner-user','owner','2026-11-01'); jid=job.id
        assert repair.enqueue(db,'owner-user','owner','2026-11-01').id==jid
        assert job.result['total']==4
        from app.queue_policy import next_job
        db.add(Job(id='older-model-check',kind='draft_recheck',created_at='2026-01-01',
                   result={'retry_draft_id':'old-draft'}));db.commit()
        assert next_job(db).id==jid
    asyncio.run(repair.step(jid,limit=2))
    with Session() as db:
        cursor=db.get(Job,jid).result['cursor_id']
        assert db.get(Job,jid).status=='queued'
    asyncio.run(repair.step(jid,limit=20))
    with Session() as db:
        job=db.get(Job,jid)
        assert job.status=='completed'
        assert (job.result['checked'],job.result['verified'])==(4,2)
        assert job.result['cursor_id']>cursor
        for rid in ('blank','praise'):
            assert db.get(Review,rid).status=='draft_ready'
            assert db.get(Draft,'d-'+rid).quality['owner_rules']['verification_method']=='local_positive'
        for rid in ('human','complaint','published','low'):
            draft=db.get(Draft,'d-'+rid)
            assert draft.revision==7 and draft.text=='Добрый день! Сохранённый ответ.'
        assert db.get(Publication,'pub').status=='uncertain'
        assert not list(db.scalars(select(Action)))
    judge.assert_not_awaited()


def test_cancelled_positive_recheck_is_not_restarted(client):
    seed()
    with Session() as db:
        job=repair.enqueue(db,'owner-user','owner');jid=job.id
        job.cancel_requested=True;db.commit()
    asyncio.run(repair.step(jid))
    with Session() as db:
        assert repair.enqueue(db,'owner-user','owner').status=='cancelled'
