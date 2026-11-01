import asyncio
from sqlalchemy import select
from app.db import Session, Review, Draft, Product, StoreProfile, User, Job, Publication, GenerationOutcome
from app import review_regeneration as regeneration, services


def seed(db):
    owner=db.scalar(select(User).where(User.role=='owner'))
    for account,market in [('owner','wb'),('oz','ozon'),('ya','yandex')]:
        if account!='owner': db.add(StoreProfile(id=account,provider=market,name=market))
        db.add(Product(id=account,name='Товар',brand='TEST'))
        db.flush()
        for suffix in ('auto','human','sent','question'):
            rid=account+suffix
            db.add(Review(id=rid,wb_review_id=rid,wb_account_id=account,product_id=account,
                rating=5,marketplace=market+('_question' if suffix=='question' else ''),text='',status='draft_ready'))
            db.flush()
            db.add(Draft(id=rid,review_id=rid,text='Старый ответ',original='Старый ответ',
                edits=[{'by':owner.id}] if suffix=='human' else []))
            if suffix=='sent': db.add(Publication(id=rid,review_id=rid,action_id=rid,status='uncertain'))
    db.commit()
    return owner.id


def test_all_market_regeneration_is_durable_preserves_manual_and_never_publishes(client):
    with Session() as db:
        actor=seed(db); created=regeneration.enqueue_all(db,actor)
        ids=[j.id for j in created]
        assert len(ids)==3 and [j.id for j in regeneration.enqueue_all(db,actor)]==ids
    for jid in ids: asyncio.run(regeneration.step(jid))
    with Session() as db:
        for account in ('owner','oz','ya'):
            auto=db.get(Draft,account+'auto')
            assert auto.text!='Старый ответ' and auto.original=='Старый ответ'
            assert auto.edits and auto.edits[-1]['by']=='automation'
            for suffix in ('human','sent','question'): assert db.get(Draft,account+suffix).text=='Старый ответ'
        assert len(list(db.scalars(select(Publication))))==3
        assert len(list(db.scalars(select(GenerationOutcome))))==3
        assert all(db.get(Job,jid).status=='completed' for jid in ids)
        revisions={d.id:d.revision for d in db.scalars(select(Draft))}
    for jid in ids: asyncio.run(regeneration.step(jid))
    with Session() as db: assert revisions=={d.id:d.revision for d in db.scalars(select(Draft))}


def test_guard_rechecks_human_edits_and_cancel_at_write_time(client):
    with Session() as db:
        actor=seed(db);job=regeneration.enqueue_all(db,actor)[0]
        account=job.result['account_id'];r=db.get(Review,account+'auto');d=db.get(Draft,account+'auto')
        recovery={'policy_regeneration':True,'job_id':job.id,'account_id':account}
        assert not services.recovery_protected(db,r,d,recovery)
        d.edits=[{'by':actor}];db.commit()
        assert services.recovery_protected(db,r,d,recovery)
        d.edits=[];job.cancel_requested=True;db.commit()
        assert services.recovery_protected(db,r,d,recovery)


def test_saved_outcome_resumes_after_crash_without_second_generation(client,monkeypatch):
    with Session() as db:
        actor=seed(db); job=regeneration.enqueue_all(db,actor)[0];jid=job.id
        rid=job.result['account_id']+'auto'
        db.add(GenerationOutcome(job_id=jid,review_id=rid));db.commit()
    async def forbidden(*args,**kwargs): raise AssertionError('Must not regenerate committed outcome')
    monkeypatch.setattr(services,'generate_draft',forbidden)
    asyncio.run(regeneration.step(jid))
    with Session() as db:
        assert db.get(Draft,rid).text=='Старый ответ'
        assert db.get(Job,jid).result['saved']==1
