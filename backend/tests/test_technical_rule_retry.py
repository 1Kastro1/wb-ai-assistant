import asyncio
import time
import pytest
from app.db import Session, Product, Review, Draft, Job, Publication
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
    assert client.get('/work-queue', params={'view':'manual'}).json()['total'] == 0
    assert client.get('/reviews/page', params={'technical_only': True}).json()['total'] == 1
    assert client.get('/reviews/page', params={'manual_only': True, 'technical_only': True}).json()['total'] == 1
    with Session() as db:
        db.get(Review, 'r').rating = 3
        db.commit()
    assert client.get('/reviews/page', params={'manual_only': True}).json()['total'] == 1
    assert client.get('/work-queue', params={'view':'manual'}).json()['total'] == 1


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


def test_explicit_saved_reply_recheck_precedes_background_scans(client):
    from app.queue_policy import next_job
    seed_error()
    with Session() as db:
        db.add(Job(id='old-policy-scan', kind='draft_recheck', created_at='2026-01-01', result={'policy_scan':True}))
        job = enqueue(db, db.get(Draft,'d'), db.get(Review,'r'), 'owner', manual=True)
        db.commit()
        assert next_job(db).id == job.id


def test_single_generation_and_newest_explicit_check_do_not_wait_for_old_retries(client):
    from app.queue_policy import next_job
    with Session() as db:
        db.add_all([Job(id='old-check',kind='draft_recheck',created_at='2026-01-01',result={'retry_draft_id':'old','manual_check':True}),
            Job(id='new-check',kind='draft_recheck',created_at='2026-11-01',result={'retry_draft_id':'new','manual_check':True}),
            Job(id='generate',kind='single_draft',created_at='2026-11-01',result={})]);db.commit()
        assert next_job(db).id=='generate'
        db.get(Job,'generate').status='completed';db.commit()
        assert next_job(db).id=='new-check'


@pytest.mark.parametrize('state', ['passed', 'pending'])
def test_stale_manual_status_does_not_override_verified_or_pending_rules(client, state):
    seed_error()
    with Session() as db:
        draft = db.get(Draft, 'd')
        draft.model = 'positive-local-2.0'
        draft.quality = {'passed': state == 'passed', 'text_issues': [], 'owner_rules': {
            'passed': state == 'passed', 'checked': state == 'passed', 'status': 'verified' if state == 'passed' else 'pending',
            'violations': [], 'issues': []}}
        db.commit()
    assert client.get('/reviews/page', params={'manual_only': True}).json()['total'] == 0
    assert client.get('/work-queue', params={'view': 'manual'}).json()['total'] == 0


def test_uncertain_publication_stays_in_personal_review_even_with_verified_positive_text(client):
    seed_error()
    with Session() as db:
        draft=db.get(Draft,'d');draft.model='positive-local'
        draft.quality={'passed':True,'text_issues':[],'owner_rules':{'passed':True,'checked':True,'violations':[]}}
        db.add(Publication(id='uncertain-publication',review_id='r',action_id='old',status='uncertain'));db.commit()
    assert client.get('/reviews/page',params={'manual_only':True}).json()['total']==1
    assert client.get('/work-queue',params={'view':'manual'}).json()['total']==1


@pytest.mark.parametrize('passed', [True, False])
def test_check_only_preserves_text_and_keeps_retrying_outages_until_cancelled(client, monkeypatch, passed):
    seed_error()
    from app import services, owner_reply_policy
    with Session() as db:
        job = enqueue(db, db.get(Draft, 'd'), db.get(Review, 'r')); jid = job.id; db.commit()
    monkeypatch.setattr(services, 'assess_reply', lambda *a, **k: {'passed': True, 'issues': [], 'score': 96})
    calls = [0]
    async def check(answer, *args, **kwargs):
        assert kwargs['check_only'] is True
        assert kwargs['check_seconds'] == 120
        calls[0] += 1
        return answer, {'passed': passed, 'checked': passed, 'technical_error': not passed,
                        'matched': 1, 'violations': [], 'issues': [] if passed else ['таймаут']}
    monkeypatch.setattr(owner_reply_policy, 'enforce_owner_rules', check)
    for _ in range(1 if passed else 12): asyncio.run(recheck_step(jid))
    with Session() as db:
        draft, job = db.get(Draft, 'd'), db.get(Job, jid)
        assert draft.revision == 7 and draft.text == 'Добрый день! Спасибо за отзыв.'
        assert draft.edits == []
        assert draft.quality['passed'] is passed
        assert job.status == ('completed' if passed else 'queued')
        if not passed:
            assert job.result['cycles'] == 12
            assert 0 < job.result['retry_at']-time.time() <= 300
            job.cancel_requested=True;db.commit()
    if not passed:
        asyncio.run(recheck_step(jid))
        with Session() as db: assert db.get(Job,jid).status=='cancelled'


def test_outage_in_scan_gets_independent_retry_and_does_not_block_next_review(client,monkeypatch):
    seed_error()
    from app import services,owner_reply_policy
    monkeypatch.setattr(services,'assess_reply',lambda *a,**k:{'passed':True,'issues':[],'score':96})
    async def check(answer,*args,**kwargs):
        return answer,{'passed':False,'checked':False,'technical_error':True,'issues':['timeout']}
    monkeypatch.setattr(owner_reply_policy,'enforce_owner_rules',check)
    with Session() as db:
        db.add(Job(id='scan',kind='draft_recheck',result={'account_id':'owner','policy_scan':True,'reviews_only':True,'check_only':True}));db.commit()
    asyncio.run(recheck_step('scan'))
    from sqlalchemy import select
    with Session() as db:
        assert db.get(Job,'scan').result['cursor_id']=='d'
        retry=db.scalar(select(Job).where(Job.result['technical_scan'].as_boolean()==True))
        assert retry and retry.id!='scan' and retry.status=='queued'
        assert db.get(Draft,'d').text=='Добрый день! Спасибо за отзыв.'


def test_transient_verification_exceptions_stop_without_losing_saved_text(client,monkeypatch):
    seed_error()
    from app import main,draft_audit
    async def unavailable(*a,**k): raise TimeoutError('model unavailable')
    monkeypatch.setattr(draft_audit,'recheck_step',unavailable)
    with Session() as db:
        job=enqueue(db,db.get(Draft,'d'),db.get(Review,'r'))
        job.max_attempts=1;job.result={**job.result,'failure_attempts':100};jid=job.id;db.commit()
    asyncio.run(main.execute_job(jid))
    with Session() as db:
        assert db.get(Job,jid).status=='failed'
        assert db.get(Job,jid).result['failure_attempts']==101
        assert db.get(Draft,'d').text=='Добрый день! Спасибо за отзыв.'


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



def test_manual_click_resumes_backoff_without_duplicate_or_approval(client, monkeypatch):
    from app import main
    from app.queue_policy import next_job
    seed_error(rating=1)
    async def no_work(): pass
    monkeypatch.setattr(main, 'run_job_queue', no_work)
    with Session() as db:
        job = enqueue(db, db.get(Draft, 'd'), db.get(Review, 'r'), 'owner', manual=True)
        job.result = {**job.result, 'retry_at': time.time() + 300, 'check_retries': 9}
        jid = job.id
        db.commit()
    response = client.post('/drafts/d/recheck-rules')
    assert response.status_code == 200 and response.json()['id'] == jid
    with Session() as db:
        job = db.get(Job, jid)
        assert job.result['retry_at'] == 0 and job.result['manual_check'] is True
        assert job.result['check_retries'] == 9
        assert next_job(db).id == jid
        assert db.get(Draft, 'd').quality['owner_rules']['passed'] is False
        assert db.get(Draft, 'd').revision == 7
    compact = client.get('/jobs?compact=true').json()
    item = next(row for row in compact if row['id'] == jid)
    assert item['result']['retry_draft_id'] == 'd'
    assert bool(item['result']['manual_check']) is True
    assert client.post('/drafts/d/recheck-rules').json()['id'] == jid


def test_manual_click_does_not_restart_running_check(client, monkeypatch):
    from app import main
    seed_error()
    async def no_work(): pass
    monkeypatch.setattr(main, 'run_job_queue', no_work)
    with Session() as db:
        job = enqueue(db, db.get(Draft, 'd'), db.get(Review, 'r'))
        job.status = 'running'
        job.result = {**job.result, 'started_at': 'original-start', 'current_draft_id': 'd'}
        jid = job.id
        db.commit()
    assert client.post('/drafts/d/recheck-rules').json()['id'] == jid
    with Session() as db:
        assert db.get(Job, jid).status == 'running'
        assert db.get(Job, jid).result['started_at'] == 'original-start'


@pytest.mark.parametrize('rating', [1, 2, 3, 4, 5])
def test_manual_recheck_has_cpu_budget_for_every_rating_without_rewriting(client, monkeypatch, rating):
    from app import services, owner_reply_policy
    from app.generation_budget import MANUAL_RULE_CHECK_SECONDS
    seed_error(rating=rating)
    with Session() as db:
        review = db.get(Review, 'r')
        review.text = 'Делал всё по инструкции. Не работает'
        review.manual = rating <= 3
        job = enqueue(db, db.get(Draft, 'd'), review, 'owner', manual=True)
        jid = job.id
        db.commit()
    monkeypatch.setattr(services, 'assess_reply', lambda *a, **k: {'passed': True, 'issues': [], 'score': 96})
    async def check(answer, review, *args, **kwargs):
        assert review.rating == rating and review.text == 'Делал всё по инструкции. Не работает'
        assert kwargs['check_seconds'] == MANUAL_RULE_CHECK_SECONDS == 180
        assert kwargs['check_only'] is True
        return answer, {'passed': True, 'checked': True, 'matched': 1, 'issues': [], 'violations': []}
    monkeypatch.setattr(owner_reply_policy, 'enforce_owner_rules', check)
    asyncio.run(recheck_step(jid))
    with Session() as db:
        draft, review = db.get(Draft, 'd'), db.get(Review, 'r')
        assert draft.text == 'Добрый день! Спасибо за отзыв.' and draft.revision == 7 and draft.edits == []
        assert review.manual == (rating <= 3)
        assert not review.is_answered
        assert db.get(Job, jid).status == 'completed'


def test_automatic_retry_backlog_does_not_starve_generation(client):
    from app.queue_policy import next_job
    with Session() as db:
        db.add_all([Job(id='auto-retry', kind='draft_recheck', created_at='2026-01-01',
            result={'retry_draft_id':'d','check_only':True,'requested_by':'owner'}),
            Job(id='batch',kind='drafts',created_at='2026-01-01',result={})])
        db.commit()
        assert next_job(db).id == 'batch'
        # An actual direct click still takes precedence over ordinary work.
        db.get(Job,'auto-retry').result={'retry_draft_id':'d','manual_check':True}
        db.commit()
        assert next_job(db).id == 'auto-retry'
