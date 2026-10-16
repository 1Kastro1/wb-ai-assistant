import asyncio

import pytest

from app import main
from app.db import Account, Job, Review, Session, StoreProfile
from app.security import guard


@pytest.mark.parametrize('fail_questions', [False, True])
def test_questions_run_between_committed_yandex_review_pages(client, monkeypatch, fail_questions):
    with Session() as db:
        db.add(StoreProfile(id='market', name='Market', provider='yandex', config={'business_id': '456'}))
        db.add(Account(id='market', encrypted_token='test-placeholder'))
        db.add(Job(id='reviews', kind='reviews', result={'account_id': 'market'}))
        db.add(Job(id='questions', kind='questions', max_attempts=1, result={'account_id': 'market'}))
        db.commit()
    monkeypatch.setattr('app.integrations.decrypt_secret', lambda value: 'test-token')
    events = []

    async def request(service, operation, **kwargs):
        if operation == 'reviews':
            page = kwargs['params'].get('pageToken')
            events.append('reviews-next' if page else 'reviews-first')
            item = {'feedbackId': 2 if page else 1, 'identifiers': {'offerId': 'SKU-1'},
                    'description': {'comment': 'Хорошо'}, 'statistics': {'rating': 5}}
            return {'result': {'feedbacks': [item], 'paging': {} if page else {'nextPageToken': 'next'}}}
        assert operation == 'questions'
        events.append('questions')
        with Session() as db:
            assert db.get(Review, 'market:1') is not None
            assert db.get(Job, 'reviews').result['checkpoint'] == {'page': 'next', 'processed': 1}
        if fail_questions:
            raise ValueError('Question API temporarily unavailable')
        return {'result': {'questions': [{'questionIdentifiers': {'id': 77, 'offerId': 'SKU-1'}, 'text': 'Подойдет?'}]}}

    monkeypatch.setattr(guard, 'request', request)
    asyncio.run(main.run_sync_job('reviews'))
    assert events == (['reviews-first', 'questions', 'reviews-next'] if fail_questions else
                      ['reviews-first', 'questions', 'questions', 'reviews-next'])
    with Session() as db:
        reviews = db.get(Job, 'reviews')
        assert reviews.status == 'completed'
        assert reviews.result['processed'] == 2
        assert db.get(Job, 'questions').status == ('failed' if fail_questions else 'completed')
        if not fail_questions:
            assert db.get(Job, 'questions').result['processed'] == 1
            assert db.get(Review, 'market:question:77').is_answered is False


def test_sync_status_shows_running_import_before_newer_queued_duplicate(client):
    with Session() as db:
        db.add_all([
            Job(id='running-import', kind='reviews', status='running', progress=95,
                created_at='2026-10-16T10:00:00+00:00', result={'account_id': 'owner', 'checkpoint': {'processed': 5000}}),
            Job(id='queued-duplicate', kind='reviews', status='queued',
                created_at='2026-10-16T11:00:00+00:00', result={'account_id': 'owner'}),
        ])
        db.commit()
    store = next(row for row in client.get('/sync/status').json()['stores'] if row['store_id'] == 'owner')
    assert store['kinds']['reviews']['job_id'] == 'running-import'
    assert store['kinds']['reviews']['state'] == 'running'
    assert store['kinds']['reviews']['processed'] == 5000
