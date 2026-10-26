import asyncio
from sqlalchemy import select

from app import main
from app.db import Session, Job, Review, Draft


def test_question_batch_cannot_suspend_review_batch_between_answers(client, seeded, monkeypatch):
    calls = []

    async def generate(db, rid, instruction=''):
        calls.append(rid)
        draft = Draft(id='fair-' + rid, review_id=rid, text='Saved', original='Saved')
        db.add(draft)
        db.commit()
        return {'id': draft.id}

    with Session() as db:
        db.add(Review(id='q1', wb_review_id='q1', product_id='p2', marketplace='wb_question',
                      text='A question', rating=0, created_at='2026-01-01'))
        db.add_all([
            Job(id='review-bulk', kind='drafts', result={'account_id': 'owner', 'selected_ids': ['r1', 'r2']}),
            Job(id='question-bulk', kind='question_drafts', result={'account_id': 'owner', 'selected_ids': ['q1']}),
        ])
        db.commit()
    monkeypatch.setattr(main, 'generate_draft', generate)
    asyncio.run(main.run_sync_job('review-bulk'))
    assert set(calls) == {'r1', 'r2'}
    with Session() as db:
        assert db.get(Job, 'review-bulk').status == 'completed'
        assert db.get(Job, 'question-bulk').status == 'queued'
        assert db.get(Job, 'review-bulk').result['processed'] == 2
    asyncio.run(main.run_job_queue())
    assert calls[-1] == 'q1'
    with Session() as db:
        assert db.get(Job, 'question-bulk').status == 'completed'
        assert len(list(db.scalars(select(Draft)))) == 3


def test_audits_and_bulk_jobs_wait_for_outer_dispatcher(client, monkeypatch):
    kinds = ('drafts', 'question_drafts', 'draft_audit', 'draft_recheck', 'knowledge_research')
    with Session() as db:
        db.add_all([Job(id=kind, kind=kind, result={'account_id': 'owner'}) for kind in kinds])
        db.commit()
    calls = []

    async def execute(jid):
        calls.append(jid)

    monkeypatch.setattr(main, 'execute_job', execute)
    asyncio.run(main.run_waiting_sync())
    assert calls == []
