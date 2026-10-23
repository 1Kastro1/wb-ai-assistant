import asyncio
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from app import main
from app.db import Draft, Job, Session


def test_queued_and_running_batches_resume_without_a_browser(seeded, monkeypatch):
    with Session() as db:
        db.add(Draft(id='already-saved', review_id='r1', text='Saved', original='Saved'))
        db.add(Job(id='all-store', kind='drafts', status='running', result={
            'account_id': 'owner', 'manual_batch': True, 'processed': 1, 'total': 4,
        }))
        db.add(Job(id='waiting', kind='drafts', status='queued', result={
            'account_id': 'owner', 'selected_ids': ['r1'], 'manual_batch': True,
        }))
        db.commit()
    calls = []

    async def generate(db, review_id, instruction=''):
        calls.append(review_id)
        draft = Draft(id='resume-' + review_id, review_id=review_id, text='Answer', original='Answer')
        db.add(draft)
        db.commit()
        return {'id': draft.id}

    monkeypatch.setattr(main, 'generate_draft', generate)
    with TestClient(main.app) as client:
        client.portal.call(asyncio.sleep, 0)
        with Session() as db:
            full = db.get(Job, 'all-store')
            assert full.status == 'completed'
            assert full.result['processed'] == full.result['total'] == 4
            assert full.progress == 100
            assert db.get(Job, 'waiting').status == 'completed'
            assert db.get(Draft, 'already-saved').text == 'Saved'
    assert set(calls) == {'r2', 'r3', 'r4'}


def test_compact_progress_keeps_scope(client, monkeypatch):
    with Session() as db:
        db.add(Job(id='selection', kind='drafts', result={
            'account_id': 'owner', 'selected_ids': ['r1'], 'since': '2026-10-01',
        }))
        db.commit()
    response = client.get('/jobs?compact=true')
    assert response.status_code == 200
    item = next(job for job in response.json() if job['id'] == 'selection')
    assert item['result']['selected_ids'] == ['r1']
    assert item['result']['since'] == '2026-10-01'


def test_check_now_is_all_store_even_with_a_queued_page_selection(client, seeded, monkeypatch):
    monkeypatch.setattr(main, 'run_job_queue', AsyncMock())
    with Session() as db:
        db.add(Job(id='page-selection', kind='drafts', result={
            'account_id': 'owner', 'selected_ids': ['r1'], 'manual_batch': True, 'total': 1,
        }))
        db.commit()
    job = client.post('/jobs/drafts', json={'manual_start': True}).json()
    assert job['id'] != 'page-selection'
    assert job['result']['total'] == 4
    assert job['result']['account_id'] == 'owner'
    assert not job['result'].get('selected_ids')
    assert not job['result'].get('since')
    assert client.post('/jobs/drafts', json={'manual_start': True}).json()['id'] == job['id']


def test_resume_does_not_count_failed_reviews_twice(client, seeded, monkeypatch):
    with Session() as db:
        db.add(Draft(id='saved-before', review_id='r1', text='Saved', original='Saved'))
        db.add(Job(id='resume-failed', kind='drafts', result={
            'account_id': 'owner', 'manual_batch': True, 'processed': 1, 'failed': 1,
            'failures': [{'review_id': 'r2', 'reason': 'Needs attention'}],
        }))
        db.commit()

    async def generate(db, review_id, instruction=''):
        assert review_id not in ('r1', 'r2')
        draft = Draft(id='remaining-' + review_id, review_id=review_id, text='Answer', original='Answer')
        db.add(draft)
        db.commit()
        return {'id': draft.id}

    monkeypatch.setattr(main, 'generate_draft', generate)
    asyncio.run(main.run_job_queue())
    with Session() as db:
        job = db.get(Job, 'resume-failed')
        assert job.status == 'completed'
        assert job.result['total'] == 4
        assert job.result['processed'] == 3
        assert job.result['failed'] == 1


def test_long_running_batch_stays_visible_after_many_newer_jobs(client):
    with Session() as db:
        db.add(Job(id='old-active', kind='drafts', status='running', created_at='2020-01-01', result={'account_id': 'owner'}))
        db.add_all(Job(id=f'recent-{i}', kind='single_draft', status='completed', created_at='2026-10-23', result={'account_id': 'owner'}) for i in range(105))
        db.commit()
    for path in ('/jobs', '/jobs?compact=true'):
        response = client.get(path)
        assert response.status_code == 200
        assert len(response.json()) == 100
        assert response.json()[0]['id'] == 'old-active'


def test_failed_background_job_records_an_important_error(client, monkeypatch):
    from app.db import Activity
    from app.activity_history import activity_page
    from sqlalchemy import select
    with Session() as db:
        db.add(Job(id='failed-import', kind='products', max_attempts=1, result={'account_id': 'owner'}))
        db.commit()

    class Provider:
        async def sync_products(self, *args, **kwargs):
            raise ValueError('Private error detail must not enter activity history')

    monkeypatch.setattr(main, 'provider_for', lambda store: Provider())
    asyncio.run(main.run_job_queue())
    with Session() as db:
        event = db.scalar(select(Activity).where(Activity.path == '/jobs/failed-import'))
        assert event.status == 500
        assert event.details == {'event': 'job_failed', 'job_kind': 'products'}
        items = activity_page(db, {'role': 'owner'})['items']
        assert items[0]['actor_name'] == 'Система'
