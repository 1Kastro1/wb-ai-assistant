import asyncio
import time
from unittest.mock import AsyncMock
from sqlalchemy import select, func
from app import main
from app.db import Session, StoreProfile, Account, Job, setting, set_setting


def connected_stores():
    with Session() as db:
        db.add(StoreProfile(id='market', name='Test market', provider='yandex'))
        db.add_all([Account(id='owner', encrypted_token='unused'), Account(id='market', encrypted_token='unused')])
        db.commit()


def test_two_intervals_are_persisted_without_resetting_hidden_settings(client, monkeypatch):
    monkeypatch.setattr(main, 'run_job_queue', AsyncMock())
    with Session() as db:
        set_setting(db, 'automation_schedule', {'products_hours': 48, 'backup_hours': 36, 'min_quality': 98})
        db.commit()
    value = client.patch('/settings/schedule', json={
        'sync_enabled': True, 'generation_enabled': True, 'sync_hours': 2, 'drafts_hours': 5,
    }).json()
    assert value['reviews_hours'] == value['questions_hours'] == value['sync_hours'] == 2
    assert value['drafts_enabled'] and value['question_drafts_enabled'] and value['enabled']
    assert (value['products_hours'], value['backup_hours'], value['min_quality']) == (48, 36, 98)
    assert client.get('/settings/schedule').json()['drafts_hours'] == 5


def test_server_ticks_each_connected_store_at_shared_intervals_without_duplicates(client, monkeypatch):
    monkeypatch.setattr(main, 'run_job_queue', AsyncMock())
    connected_stores()
    current = time.time()
    monkeypatch.setattr(main.time, 'time', lambda: current)
    response = client.patch('/settings/schedule', json={
        'sync_enabled': True, 'generation_enabled': True, 'sync_hours': 2, 'drafts_hours': 6,
    })
    assert response.status_code == 200, response.text
    with Session() as db:
        jobs = list(db.scalars(select(Job)))
        assert len(jobs) == 10
        assert {job.result['account_id'] for job in jobs} == {'owner', 'market'}
        for job in jobs:
            job.status = 'completed'
        db.commit()
    # This is the worker's tick function; there is no browser callback or request.
    main.queue_scheduled_jobs(current + 2 * 3600)
    main.queue_scheduled_jobs(current + 2 * 3600)
    with Session() as db:
        queued = list(db.scalars(select(Job).where(Job.status == 'queued')))
        assert len(queued) == 4
        assert {job.kind for job in queued} == {'reviews', 'questions'}
        for job in queued:
            job.status = 'completed'
        db.commit()
    main.queue_scheduled_jobs(current + 6 * 3600)
    with Session() as db:
        queued = list(db.scalars(select(Job).where(Job.status == 'queued')))
        assert len(queued) == 8
        assert {job.kind for job in queued} == {'reviews', 'questions', 'drafts', 'question_drafts'}


def test_manual_sync_does_not_bypass_generation_interval(client, monkeypatch):
    with Session() as db:
        set_setting(db, 'automation_schedule', {
            'enabled': True, 'sync_enabled': True, 'sync_hours': 2, 'drafts_enabled': True,
            'question_drafts_enabled': True, 'drafts_hours': 6,
            'last_drafts:owner': time.time(), 'last_question_drafts:owner': time.time(),
        })
        db.add(Job(id='import', kind='reviews', result={'account_id': 'owner'}))
        db.commit()

    class Provider:
        async def sync_reviews(self, *args, **kwargs):
            return 0

    monkeypatch.setattr(main, 'provider_for', lambda store: Provider())
    asyncio.run(main.run_sync_job('import'))
    with Session() as db:
        assert db.get(Job, 'import').status == 'completed'
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind.in_(('drafts', 'question_drafts')))) == 0


def test_pausing_automation_preserves_manual_full_batch(client, monkeypatch):
    monkeypatch.setattr(main, 'run_job_queue', AsyncMock())
    with Session() as db:
        set_setting(db, 'automation_schedule', {'enabled': True, 'drafts_enabled': True, 'question_drafts_enabled': True})
        db.add_all([
            Job(id='manual', kind='drafts', result={'account_id': 'owner', 'manual_batch': True}),
            Job(id='scheduled', kind='drafts', result={'account_id': 'owner', 'scheduled': True}),
            Job(id='questions', kind='question_drafts', result={'account_id': 'owner', 'scheduled': True}),
        ])
        db.commit()
    response = client.patch('/settings/schedule', json={'sync_enabled': False, 'generation_enabled': False})
    assert response.status_code == 200
    assert not response.json()['enabled']
    with Session() as db:
        assert db.get(Job, 'manual').status == 'queued'
        assert db.get(Job, 'scheduled').status == db.get(Job, 'questions').status == 'cancelled'


def test_waiting_backup_does_not_block_schedule_ticks(client, monkeypatch):
    connected_stores()
    with Session() as db:
        set_setting(db, 'automation_schedule', {'enabled': True, 'sync_enabled': True, 'sync_hours': 3,
                                               'last_database_check': time.time(), 'last_recovery_snapshot': 0})
        db.commit()

    async def scenario():
        async with main.maintenance_lock:
            task = asyncio.create_task(main.scheduler_maintenance())
            await asyncio.sleep(.01)
            assert not task.done()
            main.queue_scheduled_jobs()
            with Session() as db:
                assert db.scalar(select(func.count()).select_from(Job)) == 6
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_maintenance_times_do_not_overwrite_new_interval(client):
    with Session() as db:
        set_setting(db, 'automation_schedule', {'sync_hours': 8, 'drafts_hours': 12})
        db.commit()
    main.update_schedule_times({'last_database_check': 100})
    with Session() as db:
        schedule = setting(db, 'automation_schedule')
        assert schedule['sync_hours'] == 8 and schedule['drafts_hours'] == 12
        assert schedule['last_database_check'] == 100


def test_invalid_interval_and_disabled_assistant_are_rejected(client, monkeypatch):
    monkeypatch.setattr(main, 'run_job_queue', AsyncMock())
    assert client.patch('/settings/schedule', json={'sync_hours': 0}).status_code == 422
    assert client.patch('/settings/schedule', json={'drafts_hours': 169}).status_code == 422
    assert client.patch('/settings/schedule', json={'question_drafts_hours': 0}).status_code == 422
    client.patch('/settings/assistant', json={'enabled': False})
    response = client.patch('/settings/schedule', json={'generation_enabled': True})
    assert response.status_code == 400


def test_independent_intervals_tick_and_reload_without_browser(client, monkeypatch):
    monkeypatch.setattr(main, 'run_job_queue', AsyncMock())
    connected_stores()
    current = time.time()
    monkeypatch.setattr(main.time, 'time', lambda: current)
    payload = {'reviews_sync_enabled': True, 'questions_sync_enabled': True,
               'drafts_enabled': True, 'question_drafts_enabled': True,
               'reviews_hours': 2, 'questions_hours': 3, 'drafts_hours': 5, 'question_drafts_hours': 7}
    response = client.patch('/settings/schedule', json=payload)
    assert response.status_code == 200, response.text
    saved = client.get('/settings/schedule').json()
    assert all(saved[key] == value for key, value in payload.items())
    with Session() as db:
        for job in db.scalars(select(Job)):
            job.status = 'completed'
        db.commit()
    # Worker ticks independently after the settings request has finished.
    for hours, kinds in ((2, {'reviews'}), (3, {'questions'}), (5, {'reviews', 'drafts'}),
                         (7, {'reviews', 'questions', 'question_drafts'})):
        main.queue_scheduled_jobs(current + hours * 3600)
        main.queue_scheduled_jobs(current + hours * 3600)
        with Session() as db:
            jobs = list(db.scalars(select(Job).where(Job.status == 'queued')))
            assert {job.kind for job in jobs} == kinds
            assert len(jobs) == len(kinds) * 2
            for job in jobs:
                job.status = 'completed'
            db.commit()


def test_pausing_only_reviews_preserves_questions_and_manual_work(client, monkeypatch):
    monkeypatch.setattr(main, 'run_job_queue', AsyncMock())
    with Session() as db:
        set_setting(db, 'automation_schedule', {'enabled': True, 'sync_enabled': True,
                    'drafts_enabled': True, 'question_drafts_enabled': True, 'drafts_hours': 4})
        db.add_all([Job(id='r', kind='drafts', result={'scheduled': True}),
                    Job(id='q', kind='question_drafts', result={'scheduled': True}),
                    Job(id='full', kind='drafts', result={'manual_batch': True})])
        db.commit()
    response = client.patch('/settings/schedule', json={'reviews_sync_enabled': False, 'drafts_enabled': False})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['enabled'] and value['questions_sync_enabled'] and value['question_drafts_enabled']
    assert not value['reviews_sync_enabled'] and not value['drafts_enabled']
    assert value['question_drafts_hours'] == 4
    with Session() as db:
        assert db.get(Job, 'r').status == 'cancelled'
        assert db.get(Job, 'q').status == db.get(Job, 'full').status == 'queued'
    # The shortcut in Reviews must not pause question generation either.
    response = client.post('/auto-replies/pause')
    assert response.status_code == 200
    assert client.get('/settings/schedule').json()['question_drafts_enabled']


def test_review_start_leaves_questions_paused(client, monkeypatch):
    monkeypatch.setattr(main, 'run_job_queue', AsyncMock())
    with Session() as db:
        set_setting(db, 'automation_schedule', {'enabled': True, 'sync_enabled': False,
                    'drafts_enabled': False, 'question_drafts_enabled': False})
        db.commit()
    assert client.post('/auto-replies/start').status_code == 200
    value = client.get('/settings/schedule').json()
    assert value['drafts_enabled'] and not value['question_drafts_enabled']
