import asyncio
import time
from sqlalchemy import select
from app.db import Session, Product, Review, Draft, Job, User, GenerationOutcome
from app import review_regeneration as refresh, rule_retry


def seed():
    with Session() as db:
        db.add(Product(id='p',name='Товар',brand='TEST'));db.flush()
        for rid,rating,text in [('fast',5,''),('slow',1,'Не работает')]:
            db.add(Review(id=rid,wb_review_id=rid,product_id='p',wb_account_id='owner',
                marketplace='wb',rating=rating,text=text,risk='NORMAL'))
        db.commit()
        return db.scalar(select(User.id).where(User.role=='owner'))


def test_fast_answers_finish_while_contextual_generation_waits(client,monkeypatch):
    from app import services
    from app.generation_control import batch_context
    owner=seed()
    with Session() as db: jid=refresh.enqueue_all(db,owner)[0].id
    async def scenario():
        slow_started=asyncio.Event();fast_saved=asyncio.Event();release=asyncio.Event()
        async def generate(db,rid,**options):
            if rid=='slow':slow_started.set();await release.wait()
            db.add(Draft(id=rid,review_id=rid,text='Ответ',original='Ответ'))
            db.add(GenerationOutcome(job_id=batch_context.get()['job_id'],review_id=rid));db.commit()
            if rid=='fast':fast_saved.set()
        monkeypatch.setattr(services,'generate_draft',generate)
        task=asyncio.create_task(refresh.step(jid))
        await asyncio.wait_for(slow_started.wait(),2)
        await asyncio.wait_for(fast_saved.wait(),2)
        assert not task.done()
        release.set();await task
    asyncio.run(scenario())
    with Session() as db:
        assert db.get(Job,jid).status=='completed'
        assert db.get(Job,jid).result['saved']==2


def test_generation_outage_retries_same_review_without_losing_it(client,monkeypatch):
    from app import services
    from app.generation_control import batch_context
    owner=seed()
    with Session() as db:
        db.get(Review,'slow').is_answered=True;db.commit()
        jid=refresh.enqueue_all(db,owner)[0].id
    attempts=[]
    async def generate(db,rid,**options):
        attempts.append(rid)
        if len(attempts)==1:raise TimeoutError('temporary network outage')
        db.add(Draft(id=rid,review_id=rid,text='Сохранено',original='Сохранено'))
        db.add(GenerationOutcome(job_id=batch_context.get()['job_id'],review_id=rid));db.commit()
    monkeypatch.setattr(services,'generate_draft',generate)
    asyncio.run(refresh.step(jid))
    with Session() as db:
        job=db.get(Job,jid)
        assert job.status=='queued' and job.result['processed']==0 and job.result['errors']==0
        assert not job.result.get('fast_cursor')
        job.result={**job.result,'fast_retry_at':0};db.commit()
    asyncio.run(refresh.step(jid))
    with Session() as db:
        assert db.get(Job,jid).status=='completed' and db.get(Job,jid).result['saved']==1
    assert attempts==['fast','fast']


def test_full_refresh_covers_missing_drafts_and_supersedes_only_review_batches(client):
    owner=seed()
    with Session() as db:
        db.get(Review,'slow').is_answered=True
        db.add(Job(id='old',kind='drafts',result={'account_id':'owner','selected_ids':['fast']}))
        db.add(Job(id='questions',kind='question_drafts',result={'account_id':'owner'}));db.commit()
        jid=refresh.enqueue_all(db,owner)[0].id
        assert db.get(Job,'old').result['superseded_by']==jid
        assert db.get(Job,'questions').status=='queued'
    for _ in range(3):asyncio.run(refresh.step(jid))
    with Session() as db:
        assert db.scalar(select(Draft).where(Draft.review_id=='fast'))
        assert not db.scalar(select(Draft).where(Draft.review_id=='slow'))
        assert db.get(Job,jid).status=='completed'


def test_ozon_empty_ratings_are_not_expensive_unpublishable_backfill(client):
    from app.db import StoreProfile
    owner=seed()
    with Session() as db:
        db.add(StoreProfile(id='oz',provider='ozon',name='Ozon'))
        db.add(Review(id='empty-oz',wb_review_id='empty-oz',wb_account_id='oz',
            product_id='p',marketplace='ozon',text='',rating=1))
        db.add(Review(id='text-oz',wb_review_id='text-oz',wb_account_id='oz',
            product_id='p',marketplace='ozon',text='Не понравилось',rating=1))
        db.commit()
        job=next(j for j in refresh.enqueue_all(db,owner) if j.result['account_id']=='oz')
        assert job.result['total']==1


def test_one_automatic_checker_per_store_and_separate_explicit_click(client):
    seed()
    with Session() as db:
        r=db.get(Review,'fast')
        d=Draft(id='d',review_id=r.id,text='Ответ',original='Ответ');db.add(d);db.flush()
        ids={rule_retry.enqueue(db,d,r).id for _ in range(100)}
        assert len(ids)==1
        manual=rule_retry.enqueue(db,d,r,'owner',manual=True)
        assert manual.id not in ids and manual.result['manual_check']
        assert not db.get(Job,next(iter(ids))).result.get('manual_check')
        db.commit()


def test_personal_review_is_not_an_endless_technical_retry(client):
    from app.draft_audit import recheck_step
    seed()
    with Session() as db:
        review=db.get(Review,'slow');review.manual=True;review.risk='HIGH'
        draft=Draft(id='d',review_id=review.id,text='Нужна личная проверка',original='Нужна личная проверка',
            quality={'passed':False,'owner_rules':{'passed':False,'checked':False,'status':'pending'}})
        db.add(draft);db.flush();job=rule_retry.enqueue(db,draft,review);jid=job.id;db.commit()
    asyncio.run(recheck_step(jid))
    with Session() as db:
        assert db.get(Job,jid).status=='completed'
        assert not db.get(Draft,'d').quality['passed']


def test_duplicate_legacy_checks_are_superseded_without_changing_answers(client):
    seed()
    with Session() as db:
        db.add(Draft(id='d',review_id='fast',text='Ручной текст',original='Исходный',revision=8))
        for i in range(3):db.add(Job(id=str(i),kind='draft_recheck',result={
            'account_id':'owner','retry_draft_id':'d','check_only':True,'manual_check':i==2}))
        db.commit()
        assert rule_retry.consolidate(db)==2
        db.commit()
        assert all(db.get(Job,str(i)).status=='cancelled' for i in (0,1))
        assert db.get(Job,'2').status=='queued'
        assert db.get(Draft,'d').text=='Ручной текст' and db.get(Draft,'d').revision==8


def test_owner_activation_enables_all_ratings_and_all_markets(client,monkeypatch):
    from app import config,main
    from app.db import Account,set_setting,setting,StoreProfile
    from app.full_review_automation import activate
    owner=seed()
    monkeypatch.setattr(config,'REAL_PUBLISH',True)
    with Session() as db:
        set_setting(db,'test_mode',False);set_setting(db,'safe_mode',False)
        db.add(Account(id='owner',encrypted_token='unused'))
        for sid,provider in [('ya','yandex'),('oz','ozon')]:
            db.add(StoreProfile(id=sid,provider=provider,name=provider));db.add(Account(id=sid,encrypted_token='unused'))
        db.commit()
        result=activate(db,owner)
        schedule=setting(db,'automation_schedule')
        assert result['schedule']['enabled'] and schedule['drafts_all_history']
        assert schedule['publication_low_ratings'] and schedule['publication_all_low_ratings']
        assert schedule['publication_markets']=={'wb':True,'ozon':True,'yandex':True}
        assert schedule['publication_authorized_by']==owner
        assert len(result['refresh'])==3
        assert not schedule['question_drafts_enabled']
