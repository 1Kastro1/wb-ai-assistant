import asyncio
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select, func
from app import bulk_recovery as recovery, services
from app.db import Session, Action, Job, Product, Review, Draft, Publication, StoreProfile, GenerationOutcome
from app.generation_control import record_outcome


@pytest.fixture
def source(client):
    with Session() as db:
        db.add(Product(id='recover-p', name='Автотовар', brand='TEST'))
        db.add(StoreProfile(id='foreign', provider='wb', name='Другой магазин'))
        db.flush()
        for i in range(1, 8):
            db.add(Review(id=f'r{i}', wb_review_id=f'r{i}', product_id='recover-p', text='',
                          rating=5, marketplace='wb', status='draft_ready'))
        db.flush()
        for i in range(1, 8):
            db.add(Draft(id=f'd{i}', review_id=f'r{i}', text='Старый ответ', original='Старый ответ', quality={}))
        db.add(Job(id='prepare-recovery', kind='wb_publish_prepare', status='cancelled', result={'cursor': 'r7'}))
        db.add(Action(id='parent-recovery', kind='wb_publish_all', status='cancelled', payload={
            'account_id': 'owner', 'prepare_job_id': 'prepare-recovery', 'total': 1, 'excluded': 6}, result={}))
        db.add(Action(id='accepted-recovery', kind='wb_publish_manifest', status='staged', payload={
            'parent_id': 'parent-recovery', 'account_id': 'owner', 'items': [{'review_id': 'r7'}]}, result={}))
        db.commit()
    return '/publications/wb/all/parent-recovery/exclusions/recovery'


def launch(client, source, retry=False):
    response = client.post(source, json={'store_id': 'owner', 'retry_errors': retry})
    assert response.status_code == 200, response.text
    return response.json()['id']


def run(jid, limit=40):
    for _ in range(limit):
        with Session() as db:
            job = db.get(Job, jid)
            if job.status in ('completed', 'cancelled'):
                return
            job.result = {**job.result, 'retry_at': 0}
            db.commit()
        asyncio.run(recovery.step(jid))
    raise AssertionError('Recovery did not finish')


@pytest.fixture
def generate(monkeypatch):
    async def generate(db, rid, **options):
        assert options == {'recovery': 'owner'}
        draft = db.scalar(select(Draft).where(Draft.review_id == rid))
        review = db.get(Review, rid)
        draft.text = 'Добрый день! Благодарим за высокую оценку товара. Спасибо, что выбрали наш магазин!'
        draft.revision += 1
        draft.quality = {'passed': True}
        review.status = 'draft_ready'
        record_outcome(db, review, draft)
        db.commit()
        return {'id': draft.id}
    mock = AsyncMock(side_effect=generate)
    monkeypatch.setattr(services, 'generate_draft', mock)
    return mock


def test_snapshot_all_pages_no_publish_and_double_click(client, source, generate):
    jid = launch(client, source)
    assert launch(client, source) == jid
    with Session() as db:
        db.add(Review(id='r8', wb_review_id='r8', product_id='recover-p', text='', rating=5))
        db.flush(); db.add(Draft(id='d8', review_id='r8', text='Новый', original='Новый')); db.commit()
    run(jid)
    assert generate.await_count == 6
    with Session() as db:
        state = db.get(Job, jid).result
        assert (state['processed'], state['ready'], state['errors']) == (6, 6, 0)
        assert db.get(Draft, 'd7').text == 'Старый ответ'
        assert db.get(Draft, 'd8').text == 'Новый'
        assert db.scalar(select(func.count()).select_from(Publication)) == 0
        assert db.get(Action, 'parent-recovery').status == 'cancelled'
    assert client.get('/actions').json() == []


def test_preserve_manual_publications_and_do_not_approve_low_ratings(client, source, generate):
    with Session() as db:
        db.get(Draft, 'd1').edits = [{'by': 'employee'}]
        db.get(Review, 'r2').is_answered = True
        db.get(Review, 'r3').rating = 2
        db.get(Review, 'r4').safety_critical = True
        db.get(Review, 'r5').status = 'pending_confirmation'
        db.add(Publication(id='reserved', review_id='r6', action_id='old', status='uncertain'))
        db.commit()
    jid = launch(client, source); run(jid)
    assert generate.await_count == 2
    with Session() as db:
        state = db.get(Job, jid).result
        assert state['manual'] == 5 and state['ready'] == 0
        assert db.get(Draft, 'd1').text == 'Старый ответ'
        assert db.get(Draft, 'd6').text == 'Старый ответ'


def test_retry_outage_checks_saved_text_without_regenerating(client, source, generate, monkeypatch):
    original = generate.side_effect
    async def outage(db, rid, **options):
        result = await original(db, rid, **options)
        if rid == 'r1':
            db.get(Draft, result['id']).quality = {'passed': False, 'owner_rules': {
                'checked': False, 'passed': False, 'technical_error': True, 'reason': 'истекло время проверки'}}
            db.commit()
        return result
    generate.side_effect = outage
    check = AsyncMock(return_value=('Добрый день! Благодарим за высокую оценку товара. Спасибо, что выбрали наш магазин!',
        {'passed': True, 'checked': True, 'issues': [], 'matched': 0}))
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules', check)
    jid = launch(client, source); run(jid)
    assert generate.await_count == 6
    assert check.await_count == 1
    assert check.call_args.kwargs['check_only'] is True
    assert check.call_args.kwargs['check_seconds'] == 120
    with Session() as db:
        assert db.get(Job, jid).result['ready'] == 6, (db.get(Draft, 'd1').quality, db.get(Job, jid).result)
        assert db.get(Draft, 'd1').revision == 2


def test_bounded_errors_retry_only_failed_and_keep_good_drafts(client, source, generate):
    original = generate.side_effect
    async def error(db, rid, **options):
        if rid == 'r1':
            raise TimeoutError('Сервис временно недоступен')
        return await original(db, rid, **options)
    generate.side_effect = error
    jid = launch(client, source); run(jid)
    assert generate.await_count == 8
    with Session() as db:
        assert db.get(Job, jid).result['errors'] == 1
        assert db.get(Job, jid).result['ready'] == 5
    generate.side_effect = original
    second = launch(client, source, retry=True); run(second)
    with Session() as db:
        assert db.get(Job, second).result['total'] == 1
        assert db.get(Job, second).result['ready'] == 1
        assert db.get(Draft, 'd2').revision == 2


def test_stop_resume_and_authorization(client, source, generate):
    assert client.post(source, json={'store_id': 'foreign'}).status_code == 409
    assert client.post(source.replace('parent-recovery', 'unknown'), json={'store_id': 'owner'}).status_code == 404
    jid = launch(client, source)
    asyncio.run(recovery.step(jid))
    assert client.post(source+'/stop', json={'store_id': 'owner'}).json()['status'] == 'cancelled'
    assert launch(client, source) == jid
    run(jid)
    assert generate.await_count == 6


def test_crash_after_draft_commit_does_not_generate_twice(client, source, generate, monkeypatch):
    original = generate.side_effect
    async def interrupted(db, rid, **options):
        await original(db, rid, **options)
        raise asyncio.CancelledError()
    generate.side_effect = interrupted
    jid = launch(client, source)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(recovery.step(jid))
    generate.side_effect = original
    check = AsyncMock(return_value=('Добрый день! Благодарим за высокую оценку товара. Спасибо, что выбрали наш магазин!', {'passed': True, 'checked': True, 'issues': []}))
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules', check)
    run(jid)
    assert generate.await_count == 6
    with Session() as db:
        assert db.get(Draft, 'd1').revision == 2


def test_real_generator_preserves_manual_text_and_publication_race(client, source, monkeypatch):
    from app.generation_control import ReviewSkipped
    with Session() as db:
        draft = db.get(Draft, 'd1'); draft.edits = [{'by': 'owner'}]; db.commit()
        with pytest.raises(ReviewSkipped):
            asyncio.run(services.generate_draft(db, 'r1', recovery=True))
    with Session() as db:
        db.add(Publication(id='late', review_id='r2', action_id='a', status='in_flight')); db.commit()
        with pytest.raises(ReviewSkipped):
            services.edit_draft(db, 'd2', 'Добрый день! Спасибо!', 1, recovery=True)


def test_checker_only_never_applies_rewrite(client, source, monkeypatch):
    from app.owner_reply_policy import enforce_owner_rules
    from app.integrations import ollama
    mock = AsyncMock(return_value={'compliant': False, 'violations': ['Требуется иной ответ'],
                                  'revised_answer': 'Добрый день! Другой ответ.'})
    monkeypatch.setattr(ollama, 'structured', mock)
    with Session() as db:
        text = 'Добрый день! Спасибо за отзыв!'
        answer, verdict = asyncio.run(enforce_owner_rules(text, db.get(Review, 'r1'), db.get(Product, 'recover-p'),
            ['Обязательно объясняй смысл оценки своими словами.'], [], lambda value: {'passed': True}, check_only=True, check_seconds=120))
        assert answer == text and not verdict['passed']
        assert mock.await_count == 1
        assert mock.call_args.kwargs['timeout_seconds'] == 120


def test_provider_honors_background_budget_without_changing_interactive_default(monkeypatch):
    from app.integrations import OllamaProvider, guard
    request = AsyncMock(return_value={'message': {'content': '{"compliant":true}'}})
    monkeypatch.setattr(guard, 'request', request)
    provider = OllamaProvider()
    asyncio.run(provider.structured([{'role': 'user', 'content': 'check'}], {'type': 'object'}, task='OWNER_RULES', timeout_seconds=120))
    assert request.call_args.kwargs['timeout_seconds'] == 120
    asyncio.run(provider.structured([{'role': 'user', 'content': 'check'}], {'type': 'object'}, task='OWNER_RULES'))
    assert request.call_args.kwargs['timeout_seconds'] == 45

