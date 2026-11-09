import asyncio
import time
from unittest.mock import AsyncMock
import httpx
import pytest
from sqlalchemy import select, func
from app import config, services, integrations
from app.db import Session, StoreProfile, Account, Product, Review, Draft, Publication, Action, Job, set_setting, setting
from app.security import EgressGuard
from app.publication_errors import PublicationError
from app import publication_reconciliation as recovery


@pytest.mark.parametrize('failure,outcome,retryable', [
    (httpx.ConnectTimeout('timeout'), 'not_sent', True),
    (httpx.ConnectError('connect'), 'not_sent', True),
    (httpx.PoolTimeout('pool'), 'not_sent', True),
    (httpx.ReadTimeout('timeout'), 'uncertain', False),
    (httpx.WriteTimeout('timeout'), 'uncertain', False),
    (httpx.RemoteProtocolError('closed'), 'uncertain', False),
    (429, 'rejected', True), (420, 'rejected', True),
    (400, 'rejected', False), (403, 'rejected', False),
    (500, 'uncertain', False), (504, 'uncertain', False),
    (408, 'uncertain', False), (409, 'uncertain', False),
])
def test_write_failure_has_evidence_and_is_never_replayed(monkeypatch, failure, outcome, retryable):
    calls = []
    def transport(request):
        calls.append(request)
        if isinstance(failure, Exception):
            raise failure
        return httpx.Response(failure, headers={'Retry-After': '120'}, json={'error': 'do not expose'})
    guard = EgressGuard(transport=httpx.MockTransport(transport))
    monkeypatch.setattr(guard, 'validate', lambda provider, op, **kw: guard.ENDPOINTS[(provider, op)])
    with pytest.raises(PublicationError) as caught:
        asyncio.run(guard.request('ozon', 'publish', headers={'Api-Key': 'secret'}, body={'text': 'saved'}))
    assert caught.value.outcome == outcome
    assert caught.value.retryable is retryable
    assert len(calls) == 1
    assert 'secret' not in str(caught.value) and 'не отправлены' not in str(caught.value)


def test_successful_write_with_broken_json_is_uncertain(monkeypatch):
    guard = EgressGuard(transport=httpx.MockTransport(lambda req: httpx.Response(200, text='broken')))
    monkeypatch.setattr(guard, 'validate', lambda provider, op, **kw: guard.ENDPOINTS[(provider, op)])
    with pytest.raises(PublicationError) as caught:
        asyncio.run(guard.request('wb', 'publish'))
    assert caught.value.outcome == 'uncertain' and not caught.value.retryable


def test_read_connection_failure_keeps_transient_classification(monkeypatch):
    from app.batch_processing import transient
    async def no_sleep(_): pass
    monkeypatch.setattr(asyncio, 'sleep', no_sleep)
    def transport(request): raise httpx.ConnectError('offline')
    guard = EgressGuard(transport=httpx.MockTransport(transport))
    with pytest.raises(ValueError) as caught:
        asyncio.run(guard.request('ozon', 'review_comments'))
    assert transient(caught.value)


def reserve(provider='ozon', status='uncertain', store_id='store', text='Здравствуйте! Спасибо за отзыв.'):
    with Session() as db:
        db.add(StoreProfile(id=store_id, name='Shop', provider=provider, config={'business_id': '123', 'client_id': '456'}))
        db.add(Account(id=store_id, encrypted_token='encrypted-' + store_id))
        db.add(Product(id=store_id, name='Товар', marketplace=provider))
        db.flush()
        review = Review(id=store_id, product_id=store_id, marketplace=provider, wb_account_id=store_id,
            wb_review_id='12345', rating=5, text='Очень понравилось!', status='manual_review')
        db.add(review); db.flush()
        draft = Draft(id=store_id, review_id=review.id, text=text, original=text, revision=7, quality={'passed': True})
        db.add(draft)
        item = {'review_id': review.id, 'draft_id': draft.id, 'revision': draft.revision, 'text': text}
        action = Action(id=store_id, kind='publish', status='needs_review', payload={'items': [item]})
        db.add(action)
        pub = Publication(id=store_id, review_id=review.id, action_id=action.id, status=status)
        db.add(pub); db.flush()
        recovery.prepare_attempt(db, pub, review, item)
        recovery.schedule_reconciliation(db, pub, review, delay=0)
        db.commit()
    return recovery.KIND + ':' + store_id


@pytest.mark.parametrize('remote,state', [('published', 'published'), ('answer_pending', 'answer_pending'),
    ('absent', 'uncertain'), ('unknown', 'uncertain'), ('rejected', 'rejected'), ('mismatch', 'mismatch')])
def test_reconciliation_never_regenerates_or_reposts_and_keeps_edited_draft(monkeypatch, remote, state):
    jid = reserve()
    async def evidence(db, review, snapshot):
        assert snapshot['text'] == 'Здравствуйте! Спасибо за отзыв.'
        assert snapshot['revision'] == 7 and snapshot['external_id'] == '12345'
        return {'state': remote}
    monkeypatch.setattr(recovery, 'remote_evidence', evidence)
    publish = AsyncMock(); monkeypatch.setattr(integrations.ozon, 'publish', publish)
    with Session() as db:
        draft = db.get(Draft, 'store')
        draft.text, draft.revision = 'Ручная правка после отправки', 8
        db.commit()
    asyncio.run(recovery.run_step(jid))
    with Session() as db:
        assert db.get(Publication, 'store').status == state
        assert db.get(Draft, 'store').text == 'Ручная правка после отправки'
        assert db.get(Draft, 'store').revision == 8
        assert db.get(Review, 'store').is_answered is (remote == 'published')
        if remote == 'published':
            assert db.get(Review, 'store').existing_answer == 'Здравствуйте! Спасибо за отзыв.'
        assert not recovery.can_retry(db, db.get(Publication, 'store'), db.get(Review, 'store'))
        assert db.get(Job, jid).status == ('queued' if state in recovery.PENDING else 'completed')
    publish.assert_not_awaited()


def test_safe_retry_requires_proven_pre_send_failure_and_current_account(monkeypatch):
    reserve()
    with Session() as db:
        pub, review = db.get(Publication, 'store'), db.get(Review, 'store')
        recovery.record_outcome(db, pub, review, error=PublicationError('rate limit', outcome='rejected', retryable=True))
        db.commit()
        assert pub.status == 'retryable' and not recovery.can_retry(db, pub, review)
        saved = setting(db, recovery.key(pub)); saved = {**saved, 'retry_at': 0}
        set_setting(db, recovery.key(pub), saved); db.commit()
        assert recovery.can_retry(db, pub, review)
        db.get(Account, 'store').encrypted_token = 'different-connection'; db.commit()
        assert not recovery.can_retry(db, pub, review)


def test_changed_account_cannot_confirm_an_old_attempt(monkeypatch):
    jid = reserve()
    with Session() as db:
        db.get(StoreProfile, 'store').config = {'client_id': 'other'}; db.commit()
    request = AsyncMock(); monkeypatch.setattr(integrations.guard, 'request', request)
    asyncio.run(recovery.run_step(jid))
    with Session() as db:
        assert db.get(Job, jid).status == 'completed'
        assert db.get(Publication, 'store').status == 'mismatch'
        assert not db.get(Review, 'store').is_answered
    request.assert_not_awaited()


def test_job_cannot_reconcile_other_store_and_cancellation_does_not_restart(monkeypatch):
    jid = reserve()
    with Session() as db:
        job = db.get(Job, jid); job.result = {**job.result, 'account_id': 'someone-else'}; db.commit()
    request = AsyncMock(); monkeypatch.setattr(integrations.guard, 'request', request)
    asyncio.run(recovery.run_step(jid))
    with Session() as db:
        assert db.get(Job, jid).status == 'completed'
        assert db.get(Publication, 'store').status == 'uncertain'
        job = db.get(Job, jid); job.result = {**job.result, 'account_id': 'store'}; db.commit()
        job = db.get(Job, jid); job.status, job.cancel_requested = 'cancelled', True; db.commit()
        assert recovery.enqueue_due(db) == 0
        assert db.scalar(select(func.count()).select_from(Job)) == 1
    request.assert_not_awaited()


@pytest.mark.parametrize('status,state', [('PUBLISHED','published'), ('UNMODERATED','answer_pending'), ('BANNED','rejected')])
def test_yandex_requires_account_owner_review_identity_exact_text_and_status(status, state):
    comment = {'id': 20, 'feedbackId': 15, 'author': {'type': 'BUSINESS'}, 'canModify': True,
               'text': 'saved', 'status': status}
    assert recovery.yandex_comment_evidence(comment, '15', 'saved')['state'] == state
    assert recovery.yandex_comment_evidence({**comment, 'canModify': False}, '15', 'saved') is None
    assert recovery.yandex_comment_evidence({**comment, 'parentId': 1}, '15', 'saved') is None
    assert recovery.yandex_comment_evidence(comment, '15', 'different')['state'] == 'mismatch'
    with pytest.raises(ValueError): recovery.yandex_comment_evidence(comment, '16', 'saved')


def test_ozon_finds_submitted_comment_not_newer_unrelated_reply(monkeypatch):
    from app.ozon_reviews import seller_reply
    comments = [{'id': 'other', 'text': 'unrelated', 'status': 'PUBLISHED', 'is_owner': True},
                {'id': 'sent', 'text': 'saved', 'status': 'ON_MODERATION', 'is_owner': True}]
    request = AsyncMock(return_value={'comments': comments, 'has_next': False})
    monkeypatch.setattr(integrations.guard, 'request', request)
    value = asyncio.run(seller_reply({}, 'review', expected_text='saved', comment_id='sent'))
    assert value == {'state': 'answer_pending', 'text': 'saved'}


def test_yandex_reads_all_pages_and_uses_selected_store_credentials(monkeypatch):
    jid = reserve('yandex')
    seen = []
    monkeypatch.setattr(integrations, 'store_credentials', lambda db, sid: (db.get(StoreProfile, sid), 'key-' + sid))
    async def request(provider, operation, **kwargs):
        seen.append((provider, operation, kwargs))
        if len(seen) == 1:
            return {'status':'OK','result': {'comments': [{'id': 1, 'text': 'other store',
                'feedbackId': 12345, 'author': {'type':'BUSINESS'}, 'canModify': False, 'status':'PUBLISHED'}],
                'paging': {'nextPageToken':'next'}}}
        return {'status':'OK','result': {'comments': [{'id': 2, 'feedbackId': 12345,
            'author': {'type':'BUSINESS'}, 'canModify': True, 'status':'PUBLISHED',
            'text': 'Здравствуйте! Спасибо за отзыв.'}]}}
    monkeypatch.setattr(integrations.guard, 'request', request)
    asyncio.run(recovery.run_step(jid))
    assert len(seen) == 2 and all(x[1] == 'review_comments' for x in seen)
    assert seen[0][2]['headers'] == {'Api-Key':'key-store'}
    assert seen[0][2]['url'].endswith('/123/goods-feedback/comments')
    assert seen[1][2]['params']['pageToken'] == 'next'
    with Session() as db:
        assert db.get(Publication, 'store').status == 'published'


def test_retry_rechecks_current_quality_instead_of_replaying_old_action(client, monkeypatch):
    jid = reserve()
    with Session() as db:
        set_setting(db, 'safe_mode', False); set_setting(db, 'test_mode', False)
        pub, review = db.get(Publication, 'store'), db.get(Review, 'store')
        recovery.record_outcome(db, pub, review, error=PublicationError('connect', outcome='not_sent', retryable=True))
        snapshot = setting(db, recovery.key(pub)); set_setting(db, recovery.key(pub), {**snapshot, 'retry_at': 0})
        draft = db.get(Draft, 'store'); draft.quality = {'passed': False}
        action = Action(id='retry', kind='publish', payload={'items': [{'review_id':review.id, 'draft_id':draft.id,
            'revision':7,'text':draft.text}], 'test_mode':False})
        db.add(action); db.commit()
    monkeypatch.setattr(services, 'REAL_PUBLISH', True)
    monkeypatch.setattr(services, 'saved_rule_quality', lambda *args, **kwargs: {'passed': False})
    publish = AsyncMock(); monkeypatch.setattr(services, 'provider_for', lambda store: type('P', (), {'publish':publish})())
    with Session() as db, pytest.raises(ValueError, match='Правила'):
        asyncio.run(services.confirm_action(db, 'retry'))
    publish.assert_not_awaited()


def test_many_uncertain_attempts_coalesce_into_one_store_reader_and_back_off(monkeypatch):
    jid = reserve()
    with Session() as db:
        for index in range(25):
            rid = f'review-{index}'
            review = Review(id=rid, product_id='store', marketplace='ozon', wb_account_id='store',
                wb_review_id=str(index), rating=5, text='good', status='manual_review')
            db.add(review); db.flush()
            publication = Publication(id=rid, review_id=rid, action_id=rid, status='uncertain')
            db.add(publication); db.flush()
            recovery.prepare_attempt(db, publication, review, {'draft_id': rid, 'revision': 1, 'text': 'saved'})
            recovery.schedule_reconciliation(db, publication, review, delay=0)
        db.commit()
        assert db.scalar(select(func.count()).select_from(Job)) == 1
    evidence = AsyncMock(return_value={'state': 'absent'})
    monkeypatch.setattr(recovery, 'remote_evidence', evidence)
    asyncio.run(recovery.run_step(jid))
    assert evidence.await_count == 20
    asyncio.run(recovery.run_step(jid))
    assert evidence.await_count == 26
    asyncio.run(recovery.run_step(jid))
    assert evidence.await_count == 26  # No unbounded polling within backoff.
    with Session() as db:
        assert db.get(Job, jid).status == 'queued'
        assert db.get(Job, jid).result['retry_at'] > time.time()
        assert all(p.status == 'uncertain' for p in db.scalars(select(Publication)))
