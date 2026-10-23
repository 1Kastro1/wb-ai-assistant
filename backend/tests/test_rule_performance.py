import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy import select, func
from app import main
from app.db import Session, Memory, Action, Job
from app.owner_reply_policy import semantic_rules, enforce_owner_rules
from app.read_cache import cached_read, clear_reads


def test_owner_creates_rule_once_with_audience_and_metadata(client):
    for kind in ('REVIEW_RULE', 'QUESTION_RULE'):
        body = {'text': 'Не выдумывай характеристики.', 'scope': 'brand:EIKOSHA',
                'kind': kind, 'operation_id': str(uuid4())}
        created = client.post('/memory', json=body)
        assert created.status_code == 200, created.text
        assert created.json()['enabled'] and created.json()['created_by_name']
        assert client.post('/memory', json=body).json()['id'] == created.json()['id']
        assert client.post('/memory', json={**body, 'text': 'Другой текст'}).status_code == 409
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Memory)) == 2
        assert db.scalar(select(func.count()).select_from(Action)) == 0
    assert client.post('/memory', json={**body, 'operation_id': str(uuid4()), 'text': '  '}).status_code == 422
    assert client.post('/memory', json={**body, 'kind': 'GLOBAL_RULE'}).status_code == 422


def test_employee_proposes_but_cannot_create(client):
    main.app.dependency_overrides[main.authorize] = lambda: {'role': 'member', 'user_id': 'member', 'username': 'member'}
    try:
        body = {'text': 'Полезное правило', 'kind': 'QUESTION_RULE', 'scope': 'global'}
        assert client.post('/memory', json={**body, 'operation_id': str(uuid4())}).status_code == 403
        assert client.post('/memory/propose', json=body).status_code == 200
    finally:
        main.app.dependency_overrides.clear()


def test_conditional_list_retains_its_heading_and_condition():
    rule = 'Если покупатель недоволен:\n\nИзвинись за проблему.\n\nПредложи проверку.'
    assert semantic_rules([rule, rule]) == [rule]


def test_checker_preserves_context_on_repair_and_explains_invalid_verdict(monkeypatch):
    from app.integrations import ollama
    checker = AsyncMock(return_value={'compliant': False, 'violations': [], 'revised_answer': ''})
    monkeypatch.setattr(ollama, 'structured', checker)
    row = SimpleNamespace(text='', rating=5, marketplace='ozon')
    _, result = asyncio.run(enforce_owner_rules('Добрый день! Спасибо за оценку.', row,
        SimpleNamespace(name='Ароматизатор'), ['Если товар повреждён:\nИзвинись за повреждение.'], [], lambda text: {'passed': True}))
    assert not result['checked'] and not result['passed']
    assert 'некорректную' in result['reason']
    assert 'Если товар повреждён' in checker.call_args.args[0][-1]['content']


def test_compact_jobs_omit_large_snapshots_and_support_conditional_refresh(client):
    with Session() as db:
        db.add(Job(id='compact', kind='single_draft', result={'account_id': 'owner', 'processed': 1,
            'draft': {'text': 'x' * 100000}, 'selected_ids': ['private-selection']}))
        db.commit()
    first = client.get('/jobs?compact=true')
    assert first.status_code == 200
    assert first.json()[0]['result'] == {'account_id': 'owner', 'processed': 1}
    assert len(first.content) < 1000
    assert client.get('/jobs?compact=true', headers={'If-None-Match': first.headers['etag']}).status_code == 304
    with Session() as db:
        db.get(Job, 'compact').progress = 80
        db.commit()
    assert client.get('/jobs?compact=true', headers={'If-None-Match': first.headers['etag']}).status_code == 200
    assert client.get('/jobs/compact').json()['result']['draft']['text'] == 'x' * 100000


def test_read_cache_is_bounded_by_scope_and_expiry(monkeypatch):
    db = SimpleNamespace(bind=SimpleNamespace(dialect=SimpleNamespace(name='postgresql')))
    clock = [1.]
    monkeypatch.setattr('app.read_cache.monotonic', lambda: clock[0])
    clear_reads()
    calls = []
    def load():
        calls.append(1)
        return {'count': len(calls)}
    assert cached_read(db, ('store-a',), load)['count'] == 1
    value = cached_read(db, ('store-a',), load)
    value['count'] = 999
    assert cached_read(db, ('store-a',), load)['count'] == 1
    assert cached_read(db, ('store-b',), load)['count'] == 2
    clock[0] = 7.
    assert cached_read(db, ('store-a',), load)['count'] == 3
    clear_reads()


def test_http_process_leaves_durable_jobs_for_worker(client, monkeypatch):
    monkeypatch.setenv('WB_BACKGROUND_ROLE', 'api')
    with Session() as db:
        db.add(Job(id='durable', kind='drafts', result={'account_id': 'owner'}))
        db.commit()
    asyncio.run(main.run_job_queue())
    with Session() as db:
        assert db.get(Job, 'durable').status == 'queued'


def test_model_context_keeps_long_cyrillic_rules(monkeypatch):
    from app.integrations import ollama, guard
    from app.model_router import ModelRouter
    monkeypatch.setattr(ModelRouter, 'choose', lambda self, task: {'model': 'test', 'options': {'num_ctx': 8192, 'num_predict': 1024}})
    request = AsyncMock(return_value={'message': {'content': '{"compliant":true}'}})
    monkeypatch.setattr(guard, 'request', request)
    message = 'я' * 10000
    asyncio.run(ollama.chat([{'role': 'user', 'content': message}], task='OWNER_RULES'))
    body = request.call_args.kwargs['body']
    assert 8192 <= body['options']['num_ctx'] <= 16384
    assert body['messages'][0]['content'].startswith(message)
    import pytest
    with pytest.raises(ValueError, match='слишком длинные'):
        asyncio.run(ollama.chat([{'role': 'user', 'content': 'я' * 30000}], task='OWNER_RULES'))
    assert request.await_count == 1


def test_maintenance_lock_waits_and_cancellation_releases_descriptor():
    import os
    if os.name != 'posix':
        return
    from app.process_lock import ProcessLock
    async def run():
        first, second = ProcessLock('test-maintenance'), ProcessLock('test-maintenance')
        entered = asyncio.Event()
        async def wait():
            async with second:
                entered.set()
        async with first:
            task = asyncio.create_task(wait())
            await asyncio.sleep(.01)
            assert not entered.is_set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            assert second.file is None
        async with second:
            pass
    asyncio.run(run())
