import asyncio
import time
from unittest.mock import AsyncMock
import pytest

from app.db import Account, Draft, Job, Product, Publication, Review, ReviewLibraryAuditRow, Session, StoreProfile, set_setting
from app.review_minions import enqueue, run_step, candidate


@pytest.mark.parametrize('provider', ['wb', 'ozon', 'yandex'])
@pytest.mark.parametrize('proof', [
    {'technical_error': True, 'retryable': True, 'failure_code': 'timeout'},
    {'technical_error': True, 'retryable': False, 'failure_code': 'retry_exhausted'},
    {'checked': False, 'continuation': True},
])
def test_interrupted_text_check_reaches_minion_without_queueing_unsafe_reviews(provider, proof):
    from app.scheduled_publication import candidate_query

    with Session() as db:
        db.add(Product(id='p', name='Product'))
        db.flush()
        for review_id, text, safety_critical in (
            ('eligible', 'Не оправдало ожиданий', False),
            ('empty', '', False),
            ('hazard', 'Опасный случай', True),
        ):
            db.add(Review(id=review_id, wb_review_id=review_id, wb_account_id='owner',
                marketplace=provider, product_id='p', rating=3, text=text,
                status='manual_review', safety_critical=safety_critical))
            db.flush()
            db.add(Draft(id=review_id, review_id=review_id, text='Спасибо за отзыв!',
                original='Спасибо за отзыв!', quality={'passed': False, 'owner_rules': proof}))
        enqueue(db, StoreProfile(id='owner', provider=provider, name='Shop'))
        db.commit()
        assert candidate(db, db.get(Job, 'review_minion:owner'), time.time()) == 'eligible'
        schedule = {'publication_low_ratings': True}
        assert list(db.scalars(candidate_query('owner', schedule))) == []
        db.get(Draft, 'eligible').quality = {'passed': True, 'owner_rules': {'checked': True, 'passed': True}}
        db.commit()
        assert [review.id for review in db.scalars(candidate_query('owner', schedule))] == ['eligible']


def test_minion_routes_only_unpublished_safe_content_failures_and_remembers_result(monkeypatch):
    job_id = 'review_minion:owner'
    with Session() as db:
        set_setting(db, 'review_minions_enabled', True)
        set_setting(db, 'automation_schedule', {'enabled': True, 'drafts_enabled': True})
        db.add(Product(id='p', name='Product'))
        db.flush()
        for review_id, overrides in (
            ('safe', {}), ('danger', {'risk': 'HIGH', 'manual': True}),
            ('sent', {}), ('answered', {'is_answered': True}),
        ):
            db.add(Review(id=review_id, wb_review_id=review_id, wb_account_id='owner',
                marketplace='wb', product_id='p', rating=2, text='Не подошёл', status='manual_review', **overrides))
            db.flush()
            db.add(Draft(id=review_id, review_id=review_id, text='Спасибо!', original='Спасибо!',
                quality={'passed': False, 'text_issues': ['Нет ответа по существу']}))
        db.add(Publication(id='reservation', review_id='sent', action_id='existing', status='answer_pending'))
        enqueue(db, StoreProfile(id='owner', provider='wb', name='Shop'))
        db.commit()
    inspect = AsyncMock(side_effect=[TimeoutError(), {'status': 'verified'}])
    monkeypatch.setattr('app.review_library_audit.inspect_review', inspect)
    asyncio.run(run_step(job_id))
    asyncio.run(run_step(job_id))
    inspect.assert_awaited_once_with(job_id, 'safe', Session, live_minion=True)
    with Session() as db:
        job = db.get(Job, job_id)
        row = db.get(ReviewLibraryAuditRow, (job_id, 'safe'))
        assert job.status == 'queued'
        assert job.result.get('processed', 0) == 0
        assert row.status == 'retry'
        assert row.attempts == 1
        assert row.retry_at > 0
        row.retry_at = 0
        db.commit()
    asyncio.run(run_step(job_id))
    with Session() as db:
        assert db.get(Job, job_id).result['verified'] == 1
        assert db.get(ReviewLibraryAuditRow, (job_id, 'safe')).status == 'verified'
        assert db.get(Publication, 'reservation').status == 'answer_pending'
        db.get(Draft, 'safe').revision += 1
        db.commit()
        assert candidate(db, db.get(Job, job_id), time.time()) == 'safe'


def test_active_minion_owns_interrupted_text_check_instead_of_store_scan():
    from app.draft_audit import recheck_step
    from app.rule_retry import automatic_queue

    with Session() as db:
        set_setting(db, 'review_minions_enabled', True)
        set_setting(db, 'automation_schedule', {'enabled': True, 'drafts_enabled': True})
        db.add(Product(id='p', name='Product'))
        db.flush()
        db.add(Review(id='r', wb_review_id='r', product_id='p', rating=5,
            marketplace='wb', text='Очень понравилось', status='manual_review'))
        db.flush()
        db.add(Draft(id='d', review_id='r', text='Спасибо!', original='Спасибо!',
            quality={'passed': False, 'owner_rules': {'technical_error': True, 'retryable': True}}))
        enqueue(db, StoreProfile(id='owner', provider='wb', name='Shop'))
        scan_id = automatic_queue(db, 'owner').id
        db.commit()

    asyncio.run(recheck_step(scan_id))
    with Session() as db:
        assert db.get(Job, scan_id).status == 'completed'
        assert candidate(db, db.get(Job, 'review_minion:owner'), time.time()) == 'r'


def test_timeout_wakes_minion_with_priority_instead_of_store_scan():
    from app.queue_policy import next_job
    from app.rule_retry import enqueue as enqueue_recheck

    with Session() as db:
        set_setting(db, 'review_minions_enabled', True)
        set_setting(db, 'automation_schedule', {'enabled': True, 'drafts_enabled': True})
        store = StoreProfile(id='owner', provider='wb', name='Shop')
        db.add_all([store, Account(id='owner', encrypted_token='unused'), Product(id='p', name='Product')])
        db.flush()
        review = Review(id='r', wb_review_id='r', product_id='p', rating=3,
            marketplace='wb', text='Не подошло', status='manual_review')
        db.add(review)
        db.flush()
        draft = Draft(id='d', review_id='r', text='Спасибо!', original='Спасибо!',
            quality={'passed': False, 'owner_rules': {'technical_error': True, 'failure_code': 'timeout'}})
        db.add(draft)
        enqueue(db, store)
        jid = 'review_minion:owner'
        job = db.get(Job, jid)
        job.result = {**job.result, 'retry_at': time.time() + 120}
        db.add(Job(id='other', kind='drafts', result={}))
        db.flush()
        assert enqueue_recheck(db, draft, review).id == jid
        db.commit()
        assert next_job(db).id == jid
        assert candidate(db, job, time.time()) == 'r'


def test_failed_minion_self_recovers_but_explicit_stop_remains():
    with Session() as db:
        store = StoreProfile(id='owner', provider='wb', name='Shop')
        jid = 'review_minion:owner'
        enqueue(db, store)
        job = db.get(Job, jid)
        job.status = 'failed'
        job.result = {**job.result, 'failure_attempts': 2,
            'finished_at': '2020-01-01T00:00:00+00:00', 'verified': 7}
        db.commit()
        enqueue(db, store)
        assert job.status == 'queued' and job.result['verified'] == 7 and 'failure_attempts' not in job.result
        job.status, job.cancel_requested = 'cancelled', True
        db.commit()
        enqueue(db, store)
        assert job.status == 'cancelled'


def test_cancelling_active_minion_finishes_job(monkeypatch):
    from app.main import execute_job
    jid = 'review_minion:owner'
    with Session() as db:
        enqueue(db, StoreProfile(id='owner', provider='wb', name='Shop'))
        db.commit()

    async def cancel(job_id, session_factory):
        with session_factory() as db:
            db.get(Job, job_id).cancel_requested = True
            db.commit()

    monkeypatch.setattr('app.review_minions.run_step', cancel)
    asyncio.run(execute_job(jid))
    with Session() as db:
        assert db.get(Job, jid).status == 'cancelled'


def test_read_only_reconciliation_survives_generic_cancel(client):
    with Session() as db:
        db.add(Job(id='publication_reconcile:owner', kind='publication_reconcile',
            status='queued', result={'account_id': 'owner'}))
        db.commit()
    response = client.post('/jobs/publication_reconcile:owner/cancel')
    assert response.status_code == 409
    with Session() as db:
        assert db.get(Job, 'publication_reconcile:owner').status == 'queued'
