import asyncio
import pytest
from app.db import Session, Product, Review, Draft, Job
from app.draft_audit import recheck_step
from app.rule_retry import enqueue


def seed_error(rating=5, **extra):
    with Session() as db:
        db.add(Product(id='p', name='Держатель EIKOSHA', brand='EIKOSHA'))
        db.flush()
        db.add(Review(id='r', wb_review_id='r', product_id='p', rating=rating,
                      text='Все супер!', status='manual_review', risk='NORMAL'))
        db.flush()
        quality = {'passed': False, 'text_issues': [], 'owner_rules': {
            'passed': False, 'checked': False, 'technical_error': True,
            'status': 'error', 'violations': [], 'issues': ['истекло время проверки']}, **extra}
        db.add(Draft(id='d', review_id='r', text='Добрый день! Спасибо за отзыв.',
                     original='Добрый день! Спасибо за отзыв.', revision=7,
                     model='safety-fallback-1.2', quality=quality))
        db.commit()


def test_outage_is_separate_from_personal_review_and_preserves_low_rating(client):
    seed_error()
    assert client.get('/reviews/page', params={'manual_only': True}).json()['total'] == 0
    assert client.get('/reviews/page', params={'technical_only': True}).json()['total'] == 1
    assert client.get('/reviews/page', params={'manual_only': True, 'technical_only': True}).json()['total'] == 1
    with Session() as db:
        db.get(Review, 'r').rating = 3
        db.commit()
    assert client.get('/reviews/page', params={'manual_only': True}).json()['total'] == 1


@pytest.mark.parametrize('defect', ['text_issues', 'violations'])
def test_real_defects_still_need_personal_review_during_outage(client, defect):
    seed_error()
    with Session() as db:
        draft = db.get(Draft, 'd'); quality = dict(draft.quality)
        if defect == 'text_issues': quality['text_issues'] = ['Ответ не соответствует отзыву']
        else: quality['owner_rules'] = {**quality['owner_rules'], 'violations': ['Неверная подпись']}
        draft.quality = quality; db.commit()
    assert client.get('/reviews/page', params={'manual_only': True}).json()['total'] == 1


def test_enqueue_is_idempotent(client):
    seed_error()
    with Session() as db:
        draft, review = db.get(Draft, 'd'), db.get(Review, 'r')
        first = enqueue(db, draft, review); db.flush()
        assert enqueue(db, draft, review).id == first.id
        db.commit()


@pytest.mark.parametrize('passed', [True, False])
def test_check_only_preserves_text_revision_and_limits_retries(client, monkeypatch, passed):
    seed_error()
    from app import services, owner_reply_policy
    with Session() as db:
        job = enqueue(db, db.get(Draft, 'd'), db.get(Review, 'r')); jid = job.id; db.commit()
    monkeypatch.setattr(services, 'assess_reply', lambda *a, **k: {'passed': True, 'issues': [], 'score': 96})
    async def check(answer, *args, **kwargs):
        assert kwargs['check_only'] is True
        return answer, {'passed': passed, 'checked': passed, 'technical_error': not passed,
                        'matched': 1, 'violations': [], 'issues': [] if passed else ['таймаут']}
    monkeypatch.setattr(owner_reply_policy, 'enforce_owner_rules', check)
    for _ in range(1 if passed else 3): asyncio.run(recheck_step(jid))
    with Session() as db:
        draft, job = db.get(Draft, 'd'), db.get(Job, jid)
        assert draft.revision == 7 and draft.text == 'Добрый день! Спасибо за отзыв.'
        assert draft.edits == []
        assert draft.quality['passed'] is passed
        assert job.status == 'completed'
        if not passed: assert job.result['check_retries'] == 2


def test_endpoint_rejects_other_store_and_published_draft(client, monkeypatch):
    seed_error()
    from app import main
    async def no_work(): pass
    monkeypatch.setattr(main, 'run_job_queue', no_work)
    first = client.post('/drafts/d/recheck-rules')
    assert first.status_code == 200
    assert client.post('/drafts/d/recheck-rules').json()['id'] == first.json()['id']
    with Session() as db:
        db.get(Review, 'r').is_answered = True; db.commit()
    assert client.post('/drafts/d/recheck-rules').status_code == 409
    with Session() as db:
        db.get(Review, 'r').wb_account_id = 'other'; db.commit()
    assert client.post('/drafts/d/recheck-rules').status_code == 404
