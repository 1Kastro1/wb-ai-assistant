import asyncio
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select, func
from app import main, services, config, scheduled_publication as automatic
from app.db import Session, StoreProfile, Account, Product, Review, Draft, Job, Publication, Action, Setting, User, setting, set_setting


@pytest.fixture
def ready(client, monkeypatch):
    monkeypatch.setattr(main, 'run_job_queue', AsyncMock())
    monkeypatch.setattr(config, 'REAL_PUBLISH', True)
    monkeypatch.setattr(services, 'REAL_PUBLISH', True)
    calls = []
    class Provider:
        def __init__(self, store): self.store = store
        async def publish(self, db, review, text): calls.append((self.store.id, review.id, text))
    monkeypatch.setattr(services, 'provider_for', Provider)
    with Session() as db:
        set_setting(db, 'test_mode', False); set_setting(db, 'safe_mode', False)
        for store, provider in [('owner', 'wb'), ('oz', 'ozon'), ('ya', 'yandex')]:
            if store != 'owner': db.add(StoreProfile(id=store, provider=provider, name=provider))
            db.add(Account(id=store, encrypted_token='not-used-by-fake-provider'))
            db.add(Product(id=store, name='Автотовар', brand='TEST'))
            db.flush()
            for suffix, market, rating in [('review', provider, 5), ('question', provider+'_question', 0)]:
                rid = store + suffix
                db.add(Review(id=rid, wb_review_id=rid, wb_account_id=store, product_id=store,
                    marketplace=market, rating=rating, text='', status='draft_ready', risk='NORMAL'))
                db.flush()
                text = 'Добрый день! Спасибо за высокую оценку!'
                db.add(Draft(id=rid, review_id=rid, text=text, original=text, revision=9, quality={'passed': True}))
        db.commit()
    return calls


def enable(client, **extra):
    result = client.patch('/settings/schedule', json={'publication_enabled': True, 'publication_hours': 3,
        'publication_reviews': True, 'publication_questions': True, **extra})
    assert result.status_code == 200, result.text
    return result.json()


def jobs():
    with Session() as db: return list(db.scalars(select(Job.id).where(Job.kind == automatic.KIND)))


def test_disabled_by_default_and_enabling_enqueues_every_connected_market_once(client, ready):
    assert not client.get('/settings/schedule').json()['publication_enabled']
    result = enable(client)
    assert result['enabled'] and result['publication_hours'] == 3
    assert 'publication_authorized_by' not in result and 'publication_authorization' not in result
    assert len(result['publication_runtime']['stores']) == 3
    assert len(jobs()) == 3 and not ready
    main.queue_scheduled_jobs()
    assert len(jobs()) == 3
    for jid in jobs(): asyncio.run(automatic.run_step(jid))
    assert len(ready) == 6 and {row[0] for row in ready} == {'owner', 'oz', 'ya'}
    for jid in jobs(): asyncio.run(automatic.run_step(jid))
    assert len(ready) == 6  # completed jobs and existing publication slots cannot replay
    with Session() as db:
        assert all(job.status == 'completed' for job in db.scalars(select(Job)))
        assert all(d.text == d.original and d.revision == 9 and not d.edits for d in db.scalars(select(Draft)))


@pytest.mark.parametrize('change', ['negative', 'manual', 'safety', 'pending', 'bad_text', 'catalog', 'old_publication'])
def test_automation_excludes_unready_and_personal_review_cases(client, ready, change):
    with Session() as db:
        review, draft = db.get(Review, 'ownerreview'), db.get(Draft, 'ownerreview')
        if change == 'negative': review.rating = 2
        if change == 'manual': review.manual = True
        if change == 'safety': review.safety_critical = True
        if change == 'pending': draft.quality = {'passed': True, 'owner_rules': {'checked': False, 'passed': False}}
        if change == 'bad_text': draft.text = 'Купите ещё!'
        if change == 'catalog':
            review.marketplace = 'wb_question'; draft.quality = {'passed': True, 'fitment': {'status': 'CONFLICT'}}
        if change == 'old_publication': db.add(Publication(id='old', review_id=review.id, action_id='old', status='uncertain'))
        db.commit()
    enable(client)
    for jid in jobs(): asyncio.run(automatic.run_step(jid))
    assert 'ownerreview' not in {row[1] for row in ready}


def test_disable_during_first_request_stops_further_publications(client, ready, monkeypatch):
    class Provider:
        async def publish(self, db, review, text):
            ready.append(review.id)
            with Session() as other:
                row = other.get(Setting, 'automation_schedule')
                row.value = {**row.value, 'publication_enabled': False}; other.commit()
    monkeypatch.setattr(services, 'provider_for', lambda store: Provider())
    enable(client)
    for jid in jobs(): asyncio.run(automatic.run_step(jid))
    assert len(ready) == 1


def test_uncertain_result_stops_store_without_retrying_sent_request(client, ready, monkeypatch):
    class Provider:
        async def publish(self, db, review, text):
            ready.append(review.id); raise ValueError('Marketplace timeout')
    monkeypatch.setattr(services, 'provider_for', lambda store: Provider())
    enable(client)
    jid = jobs()[0]; asyncio.run(automatic.run_step(jid))
    with Session() as db:
        job = db.get(Job, jid)
        assert job.result['uncertain'] == 1
        job.status = 'queued'; db.commit()  # recovery after a process restart
    asyncio.run(automatic.run_step(jid))
    assert len(ready) == 1


def test_changed_revision_is_not_sent_from_old_action_snapshot(client, ready, monkeypatch):
    original = services.confirm_action
    async def changed(db, aid, **kwargs):
        action = db.get(Action, aid)
        with Session() as other:
            draft = other.get(Draft, action.payload['items'][0]['draft_id'])
            draft.revision += 1; other.commit()
        return await original(db, aid, **kwargs)
    monkeypatch.setattr(services, 'confirm_action', changed)
    enable(client)
    for jid in jobs(): asyncio.run(automatic.run_step(jid))
    assert not ready


def test_turning_off_cancels_old_jobs_and_reenable_requires_new_authorization(client, ready):
    enable(client); old_jobs = jobs()
    with Session() as db: first = setting(db, 'automation_schedule')['publication_authorization']
    assert client.patch('/settings/schedule', json={'publication_enabled': False}).status_code == 200
    for jid in old_jobs: asyncio.run(automatic.run_step(jid))
    assert not ready
    enable(client)
    with Session() as db:
        assert setting(db, 'automation_schedule')['publication_authorization'] != first
        assert all(db.get(Job, jid).status == 'cancelled' for jid in old_jobs)


def test_work_mode_owner_and_nonempty_scope_are_required(client, ready):
    assert client.patch('/settings/schedule', json={'publication_enabled':True,'publication_reviews':False,'publication_questions':False}).status_code == 400
    with Session() as db: set_setting(db, 'safe_mode', True); db.commit()
    assert client.patch('/settings/schedule', json={'publication_enabled':True}).status_code == 400
    with Session() as db:
        set_setting(db, 'safe_mode', False)
        db.scalar(select(User).where(User.role == 'owner')).role = 'member'; db.commit()
    assert client.patch('/settings/schedule', json={'publication_enabled':True}).status_code == 403


def test_actor_revocation_blocks_an_already_queued_job(client, ready):
    enable(client)
    with Session() as db:
        db.scalar(select(User).where(User.role == 'owner')).enabled = False; db.commit()
    for jid in jobs(): asyncio.run(automatic.run_step(jid))
    assert not ready


def test_inflight_slot_after_crash_is_reported_and_never_reposted(client, ready):
    enable(client); jid = jobs()[0]
    with Session() as db:
        store_id = db.get(Job, jid).result['account_id']
        rid = store_id+'review'
        db.add(Action(id='interrupted', kind='publish', status='executing', payload={'scheduled_job_id':jid}))
        db.add(Publication(id='reserved', review_id=rid, action_id='interrupted', scheduled_job_id=jid, status='in_flight'))
        db.get(Review, rid).status = 'publishing'; db.commit()
        assert next(s for s in automatic.runtime(db, setting(db,'automation_schedule'))['stores'] if s['id']==store_id)['uncertain']==1
    asyncio.run(automatic.run_step(jid))
    assert not ready


def test_checkpoint_resume_keeps_exact_counts_and_never_repeats_a_reply(client, ready, monkeypatch):
    monkeypatch.setattr(automatic, 'BATCH_SIZE', 1)
    enable(client)
    for _ in range(4):
        for jid in jobs(): asyncio.run(automatic.run_step(jid))
    assert len(ready) == 6 and len({row[1] for row in ready}) == 6
    with Session() as db:
        assert all(job.result['published']==2 and job.status=='completed' for job in db.scalars(select(Job)))


@pytest.mark.parametrize('reviews,questions', [(True,False),(False,True)])
def test_only_selected_answer_types_are_published(client, ready, reviews, questions):
    enable(client,publication_reviews=reviews,publication_questions=questions)
    for jid in jobs(): asyncio.run(automatic.run_step(jid))
    assert len(ready) == 3
    assert all(row[1].endswith('review' if reviews else 'question') for row in ready)


def test_updated_rules_block_old_verified_drafts(client, ready):
    from app.db import Memory
    enable(client)
    with Session() as db:
        db.add(Memory(id='signature',kind='GLOBAL_RULE',scope='global',enabled=True,
            text='Всегда добавляй подпись: С уважением, ГК Юником.'));db.commit()
    for jid in jobs(): asyncio.run(automatic.run_step(jid))
    assert not ready
