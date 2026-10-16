import asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main
from app.db import Account, Draft, Job, Session, StoreProfile, set_setting


def test_sync_runs_between_bulk_drafts(client, seeded, monkeypatch):
    events = []

    async def generate(db, review_id, instruction=''):
        events.append('draft')
        draft = Draft(id='queue-' + review_id, review_id=review_id, text='Ответ', original='Ответ')
        db.add(draft)
        db.commit()
        return {'id': draft.id}

    class Provider:
        async def sync_reviews(self, db, account_id, *args, **kwargs):
            events.append('sync')
            # A separate import session sees the committed draft.
            assert db.scalar(select(Draft.id))
            return 3

    with Session() as db:
        db.add_all([
            StoreProfile(id='market', provider='yandex', name='Market'),
            Job(id='bulk', kind='drafts', priority=50, result={'account_id': 'owner'}),
            Job(id='import', kind='reviews', result={'account_id': 'market'}),
        ])
        db.commit()
    monkeypatch.setattr(main, 'generate_draft', generate)
    monkeypatch.setattr(main, 'provider_for', lambda store: Provider())
    asyncio.run(main.run_job_queue())
    assert events == ['draft', 'sync', 'draft', 'draft', 'draft']
    with Session() as db:
        assert db.get(Job, 'bulk').result['processed'] == 4
        assert db.get(Job, 'import').status == 'completed'
        assert db.get(Job, 'import').result['processed'] == 3


def test_repeated_sync_reuses_job_only_in_same_store(client, monkeypatch):
    async def no_queue():
        pass

    monkeypatch.setattr(main, 'run_job_queue', no_queue)
    with Session() as db:
        db.add_all([
            StoreProfile(id='market', provider='yandex', name='Market'),
            Account(id='market', encrypted_token='unused'),
            Job(id='other-import', kind='reviews', result={'account_id': 'owner'}),
        ])
        db.commit()
    client.post('/stores/market/activate')
    first = client.post('/jobs/sync/reviews').json()
    second = client.post('/jobs/sync/reviews').json()
    assert first['id'] == second['id']
    assert first['id'] != 'other-import'
    assert client.post('/jobs/sync/questions').json()['id'] != first['id']


def test_recovered_sync_starts_with_automation_disabled(monkeypatch):
    with Session() as db:
        db.add(StoreProfile(id='market', provider='yandex', name='Market'))
        db.add(Job(id='recovered', kind='reviews', status='running', result={'account_id': 'market', 'checkpoint': {'processed': 5}}))
        set_setting(db, 'automation_schedule', {'enabled': False})
        db.commit()
    started = []

    class Provider:
        async def sync_reviews(self, db, account_id, *args, resume=None, **kwargs):
            started.append(resume)
            return 7

    monkeypatch.setattr(main, 'provider_for', lambda store: Provider())
    with TestClient(main.app) as c:
        # Flush the startup worker on its own event loop, without running the
        # dispatcher from the test (which would hide a missing startup wakeup).
        c.portal.call(asyncio.sleep, 0)
        assert started == [{'processed': 5}]
        with Session() as db:
            assert db.get(Job, 'recovered').status == 'completed'


def test_worker_shutdown_preserves_import_checkpoint(client, monkeypatch):
    with Session() as db:
        db.add(Job(id='shutdown-import', kind='reviews', result={'account_id': 'owner'}))
        db.commit()

    class Provider:
        async def sync_reviews(self, db, account_id, *args, checkpoint=None, **kwargs):
            checkpoint({'processed': 5, 'page': 'next'})
            asyncio.current_task().cancel()
            await asyncio.sleep(0)

    monkeypatch.setattr(main, 'provider_for', lambda store: Provider())

    async def shutdown():
        task = asyncio.create_task(main.run_sync_job('shutdown-import'))
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(shutdown())
    with Session() as db:
        job = db.get(Job, 'shutdown-import')
        assert job.status == 'queued'
        assert job.result['checkpoint'] == {'processed': 5, 'page': 'next'}
