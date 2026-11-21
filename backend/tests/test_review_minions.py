import asyncio
from unittest.mock import AsyncMock

from app.db import Draft, Job, Product, Publication, Review, ReviewLibraryAuditRow, Session, StoreProfile, set_setting
from app.review_minions import enqueue, run_step


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


def test_read_only_reconciliation_survives_generic_cancel(client):
    with Session() as db:
        db.add(Job(id='publication_reconcile:owner', kind='publication_reconcile',
            status='queued', result={'account_id': 'owner'}))
        db.commit()
    response = client.post('/jobs/publication_reconcile:owner/cancel')
    assert response.status_code == 409
    with Session() as db:
        assert db.get(Job, 'publication_reconcile:owner').status == 'queued'
