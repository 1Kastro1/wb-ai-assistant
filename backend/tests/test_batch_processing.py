import asyncio
from collections import Counter
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select
from app import main, batch_processing as batch
from app.db import Session, Review, Draft, Job, GenerationClaim
from app.generation_control import exclusive_generation, GenerationBusy, FastPathUnavailable


def save(db, rid, *, manual=False):
    draft = Draft(id='answer-' + rid, review_id=rid, text='Saved', original='Saved')
    db.add(draft)
    db.get(Review, rid).status = 'manual_review' if manual else 'draft_ready'
    db.commit()
    return {'id': draft.id}


def start_job(**result):
    with Session() as db:
        db.add(Job(id='batch', kind='drafts', result={'account_id': 'owner', **result}))
        db.commit()


def test_fast_answers_finish_while_model_is_blocked_and_new_reviews_join(client, seeded, monkeypatch):
    with Session() as db:
        db.get(Review, 'r1').created_at = '2026-01-01'
        db.get(Review, 'r2').created_at = '2026-01-02'
        db.get(Review, 'r3').created_at = '2026-01-03'
        review = db.get(Review, 'r4')
        review.text, review.rating, review.manual, review.risk = '', 5, False, 'NORMAL'
        db.commit()
    start_job()
    events = []

    async def scenario():
        model_started, release = asyncio.Event(), asyncio.Event()

        async def generate(db, rid, instruction='', *, fast_only=False, skip_existing=False):
            events.append((rid, fast_only))
            if rid == 'r3':
                model_started.set()
                await release.wait()
            return save(db, rid)

        monkeypatch.setattr(main, 'generate_draft', generate)
        task = asyncio.create_task(main.run_sync_job('batch'))
        await asyncio.wait_for(model_started.wait(), 2)
        for _ in range(100):
            with Session() as db:
                if db.get(Draft, 'answer-r4'):
                    break
            await asyncio.sleep(.01)
        else:
            raise AssertionError('Fast lane waited for the model')
        with Session() as db:
            db.add(Review(id='new', wb_review_id='new', product_id='p2', text='Новый отзыв', rating=5, created_at='2027-01-01'))
            db.commit()
        release.set()
        await asyncio.wait_for(task, 3)

    asyncio.run(scenario())
    assert events[0] == ('r4', True)
    assert [rid for rid, fast in events if not fast] == ['r3', 'new', 'r2', 'r1']
    with Session() as db:
        result = db.get(Job, 'batch').result
        assert result['processed'] == result['total'] == 5
        assert result['current_reviews'] == []


def test_cancel_interrupts_inflight_generation_and_keeps_saved_answers(client, seeded, monkeypatch):
    start_job()

    async def scenario():
        started, cancelled = asyncio.Event(), asyncio.Event()
        calls = 0

        async def generate(db, rid, instruction=''):
            nonlocal calls
            calls += 1
            if calls == 1:
                return save(db, rid)
            started.set()
            try:
                await asyncio.sleep(600)
            finally:
                cancelled.set()

        monkeypatch.setattr(main, 'generate_draft', generate)
        task = asyncio.create_task(main.run_sync_job('batch'))
        await asyncio.wait_for(started.wait(), 2)
        with Session() as db:
            db.get(Job, 'batch').cancel_requested = True
            db.commit()
        await asyncio.wait_for(task, 2)
        assert cancelled.is_set()

    asyncio.run(scenario())
    with Session() as db:
        assert db.get(Job, 'batch').status == 'cancelled'
        assert db.get(Job, 'batch').result['processed'] == 1
        assert len(list(db.scalars(select(Draft)))) == 1


def test_retry_skip_manual_and_technical_counts_are_distinct(client, seeded, monkeypatch):
    start_job()
    monkeypatch.setattr(batch, 'RETRY_DELAYS', (0, 0))
    calls = Counter()

    async def generate(db, rid, instruction=''):
        calls[rid] += 1
        if rid == 'r1':
            raise ValueError('Отзыв не найден или уже имеет ответ')
        if rid == 'r2':
            raise ConnectionError('offline')
        if rid == 'r3' and calls[rid] == 1:
            raise TimeoutError('timeout')
        return save(db, rid, manual=rid == 'r4')

    monkeypatch.setattr(main, 'generate_draft', generate)
    asyncio.run(main.run_sync_job('batch'))
    with Session() as db:
        result = db.get(Job, 'batch').result
        assert (result['processed'], result['skipped'], result['failed'], result['attention']) == (2, 1, 1, 1)
        assert result['total'] == 4
        assert result['retrying'] == 0
        assert result['failures'][0]['kind'] == 'technical'
    assert calls == {'r1': 1, 'r2': 3, 'r3': 2, 'r4': 1}


def test_unknown_rules_leave_fast_lane_without_becoming_an_error(client, seeded, monkeypatch):
    with Session() as db:
        review = db.get(Review, 'r4')
        review.text, review.rating, review.manual, review.risk = '', 5, False, 'NORMAL'
        db.commit()
    start_job(selected_ids=['r4'])
    routes = []

    async def generate(db, rid, instruction='', *, fast_only=False):
        routes.append(fast_only)
        if fast_only:
            raise FastPathUnavailable()
        return save(db, rid)

    monkeypatch.setattr(main, 'generate_draft', generate)
    asyncio.run(main.run_sync_job('batch'))
    assert routes == [True, False]
    with Session() as db:
        assert db.get(Job, 'batch').result['failed'] == 0


def test_selected_requests_reuse_full_run_or_exclude_overlapping_ids(client, seeded, monkeypatch):
    monkeypatch.setattr(main, 'run_job_queue', AsyncMock())
    first = client.post('/jobs/selected-drafts', json={'review_ids': ['r1', 'r2']}).json()
    second = client.post('/jobs/selected-drafts', json={'review_ids': ['r2', 'r3']}).json()
    assert second['result']['selected_ids'] == ['r3']
    full = client.post('/jobs/drafts', json={'manual_start': True}).json()
    repeated = client.post('/jobs/selected-drafts', json={'review_ids': ['r1', 'r4']}).json()
    assert repeated['id'] == full['id']
    assert first['id'] != full['id']


def test_generation_claim_prevents_duplicates_and_releases_on_cancel(client):
    async def scenario():
        started = asyncio.Event()

        async def slow():
            started.set()
            await asyncio.sleep(600)

        task = asyncio.create_task(exclusive_generation('review', slow))
        await started.wait()
        with pytest.raises(GenerationBusy):
            await exclusive_generation('review', slow)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await exclusive_generation('review', AsyncMock(return_value='saved')) == 'saved'

    asyncio.run(scenario())
    with Session() as db:
        assert db.get(GenerationClaim, 'review') is None


def test_cancel_is_observed_during_between_review_sync(client, seeded, monkeypatch):
    start_job()

    async def scenario():
        syncing = asyncio.Event()

        async def generate(db, rid, instruction=''):
            return save(db, rid)

        async def between():
            syncing.set()
            await asyncio.sleep(600)

        monkeypatch.setattr(main, 'generate_draft', generate)
        monkeypatch.setattr(main, 'run_waiting_sync', between)
        task = asyncio.create_task(main.run_sync_job('batch'))
        await asyncio.wait_for(syncing.wait(), 2)
        with Session() as db:
            db.get(Job, 'batch').cancel_requested = True
            db.commit()
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())
    with Session() as db:
        assert db.get(Job, 'batch').status == 'cancelled'
        assert db.get(Job, 'batch').result['processed'] == 1


def test_fast_only_never_calls_model_for_unreviewed_policy(client, seeded, monkeypatch):
    from app.services import generate_draft
    from app.integrations import ollama
    from app.db import Memory
    calls = AsyncMock(side_effect=AssertionError('Fast lane called a model'))
    for name in ('structured', 'chat', 'reply_variants'):
        monkeypatch.setattr(ollama, name, calls)
    with Session() as db:
        review = db.get(Review, 'r4')
        review.text, review.rating, review.risk, review.manual = '', 5, 'NORMAL', False
        db.add(Memory(id='unknown', kind='REVIEW_RULE', text='Всегда сообщай точный код упаковки из карточки.'))
        db.commit()
        with pytest.raises(FastPathUnavailable):
            asyncio.run(generate_draft(db, 'r4', fast_only=True, skip_existing=True))
        assert db.scalar(select(Draft.id).where(Draft.review_id == 'r4')) is None
    calls.assert_not_called()


def test_expired_claim_can_be_recovered_after_worker_crash(client):
    with Session() as db:
        db.add(GenerationClaim(review_id='expired', token='old-worker', expires_at=0))
        db.commit()
    assert asyncio.run(exclusive_generation('expired', AsyncMock(return_value='recovered'))) == 'recovered'


def test_fast_lane_continues_during_synchronization(client, seeded, monkeypatch):
    with Session() as db:
        for review in db.scalars(select(Review)):
            review.text, review.rating, review.risk, review.manual, review.safety_critical = '', 5, 'NORMAL', False, False
        db.commit()
    start_job()

    async def scenario():
        syncing, release = asyncio.Event(), asyncio.Event()

        async def generate(db, rid, instruction='', *, fast_only=False):
            assert fast_only
            return save(db, rid)

        async def between():
            syncing.set()
            await release.wait()

        monkeypatch.setattr(main, 'generate_draft', generate)
        monkeypatch.setattr(main, 'run_waiting_sync', between)
        task = asyncio.create_task(main.run_sync_job('batch'))
        await asyncio.wait_for(syncing.wait(), 2)
        for _ in range(100):
            with Session() as db:
                if len(list(db.scalars(select(Draft)))) == 4:
                    break
            await asyncio.sleep(.01)
        else:
            raise AssertionError('Synchronization blocked the fast lane')
        release.set()
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


def test_resume_recovers_answer_committed_before_checkpoint(client, seeded, monkeypatch):
    from app.db import GenerationOutcome
    start_job(selected_ids=['r4'], processed=0)
    with Session() as db:
        save(db, 'r4', manual=True)
        db.add(GenerationOutcome(job_id='batch', review_id='r4', attention=True))
        db.commit()
    generate = AsyncMock(side_effect=AssertionError('Saved answer regenerated'))
    monkeypatch.setattr(main, 'generate_draft', generate)
    asyncio.run(main.run_sync_job('batch'))
    with Session() as db:
        result = db.get(Job, 'batch').result
        assert result['processed'] == result['total'] == result['attention'] == 1
        assert result['accounted_outcomes'] == 1


def test_real_fast_route_commits_answer_and_recovery_record_together(client, seeded, monkeypatch):
    from app.db import Memory, GenerationOutcome
    from app.integrations import ollama
    calls = AsyncMock(side_effect=AssertionError('Fast reply called the model'))
    for name in ('structured', 'chat', 'reply_variants'):
        monkeypatch.setattr(ollama, name, calls)
    with Session() as db:
        review = db.get(Review, 'r4')
        review.text, review.rating, review.risk, review.manual = '', 5, 'NORMAL', False
        db.add(Memory(id='greeting', kind='REVIEW_RULE', text='Каждый ответ начинай с «Добрый день!».'))
        db.commit()
    start_job(selected_ids=['r4'])
    asyncio.run(main.run_sync_job('batch'))
    with Session() as db:
        job = db.get(Job, 'batch')
        assert job.status == 'completed'
        assert job.result['processed'] == 1
        assert db.get(GenerationOutcome, ('batch', 'r4')) is not None
        assert db.scalar(select(Draft.id).where(Draft.review_id == 'r4')) is not None
    calls.assert_not_called()
