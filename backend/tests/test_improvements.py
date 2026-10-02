import asyncio
import pytest
from types import SimpleNamespace
from sqlalchemy import select
from app.db import Session, Product, Review, Draft, Job, Account, Action, setting, set_setting
from app.services import assess_reply, edit_draft, QUALITY_EVALUATOR_VERSION
from app.integrations import web_research


def test_quality_gate_understands_wrong_item():
    review = SimpleNamespace(text='Пришёл не тот товар', rating=1)
    product = SimpleNamespace(name='Очиститель')
    weak = assess_reply(review, product, 'Спасибо за отзыв!')
    useful = assess_reply(review, product, 'Здравствуйте! Нам очень жаль, что пришёл не тот товар. Вы можете оформить возврат товара через Wildberries. Спасибо, что сообщили о ситуации.')
    assert weak['score'] < 70
    assert useful['score'] >= 70


@pytest.mark.parametrize('text,expected', [
    ('Воняет, очень неприятный запах', 'ODOR_COMPLAINT'),
    ('Запаха почти нет', 'WEAK_SCENT'),
    ('Товар не соответствует описанию', 'DESCRIPTION_MISMATCH'),
    ('Коробка помята и упаковка вскрыта', 'PACKAGING_ISSUE'),
    ('Как пользоваться этим средством?', 'USAGE_QUESTION'),
    ('Отличный товар, рекомендую', 'POSITIVE_EXPERIENCE'),
])
def test_extended_review_intents(text, expected):
    from app.safety import review_intent
    assert review_intent(text) == expected


def test_quality_includes_explainable_breakdown():
    review = SimpleNamespace(text='Запах оказался неприятным', rating=1)
    product = SimpleNamespace(name='Очиститель')
    result = assess_reply(review, product, 'Здравствуйте! Спасибо за честный отзыв. Нам искренне жаль, что запах средства показался неприятным. Мы обязательно учтём ваше замечание об аромате товара. Благодарим за обратную связь.')
    assert result['intent'] == 'ODOR_COMPLAINT'
    assert result['intent_label'] == 'неприятный запах'
    assert result['breakdown']['safety']['score'] == 40
    assert result['breakdown']['relevance']['score'] == 12
    assert result['evaluator_version'] == QUALITY_EVALUATOR_VERSION


def test_quality_report_rechecks_old_scores(client, seeded):
    with Session() as db:
        db.add(Draft(id='old-score', review_id='r1', text='Спасибо за отзыв.', original='Спасибо за отзыв.', quality={'score': 99, 'passed': True}))
        db.commit()
    assert client.get('/quality-report').status_code == 200
    with Session() as db:
        quality = db.get(Draft, 'old-score').quality
        assert quality['evaluator_version'] == QUALITY_EVALUATOR_VERSION
        assert quality['score'] < 99


def test_owner_feedback_is_saved_for_learning(client, seeded):
    with Session() as db:
        db.add(Draft(id='feedback-draft', review_id='r1', text='Подробный ответ покупателю.', original='Подробный ответ покупателю.'))
        db.commit()
    response = client.post('/drafts/feedback-draft/feedback', json={'rating':'negative','reasons':['слишком общий']})
    assert response.status_code == 200
    with Session() as db:
        assert db.get(Draft, 'feedback-draft').quality['owner_feedback']['rating'] == 'negative'
        assert setting(db, 'reply_feedback:owner')['reasons']['слишком общий'] == 1


def test_system_status_and_auto_reply_window(client, seeded, monkeypatch):
    async def no_queue():
        return None
    monkeypatch.setattr('app.main.run_job_queue', no_queue)
    status = client.get('/system/status')
    assert status.status_code == 200
    assert 'database_mb' in status.json() and 'scheduler' in status.json()
    started = client.post('/auto-replies/start')
    assert started.status_code == 200
    with Session() as db:
        job = db.scalar(select(Job).where(Job.kind == 'drafts').order_by(Job.created_at.desc()))
        assert job.result['since']
        assert setting(db, 'automation_schedule')['drafts_days'] == 7


def test_edit_is_learned_and_rescored(client, seeded):
    with Session() as db:
        db.add(Draft(id='learn', review_id='r1', text='Спасибо за отзыв.', original='Спасибо за отзыв.'))
        db.commit()
    response = client.patch('/drafts/learn', json={'text': 'Здравствуйте! Нам жаль, что колодки скрипят. Спасибо за обратную связь.', 'revision': 1})
    assert response.status_code == 200
    with Session() as db:
        assert setting(db, 'learned_edit_preferences:owner')['edit_count'] == 1
        assert db.get(Draft, 'learn').quality['score'] > 0


def test_review_and_product_pagination(client, seeded):
    reviews = client.get('/reviews/page?limit=2').json()
    assert len(reviews['items']) == 2 and reviews['total'] == 4 and reviews['has_more']
    products = client.get('/products/page?limit=1&q=Колодки').json()
    assert products['total'] == 1 and products['items'][0]['id'] == 'p1'


def test_store_profiles_isolate_review_lists(client, seeded):
    store = client.post('/stores', json={'name': 'Второй магазин'}).json()
    client.post(f"/stores/{store['id']}/activate")
    assert client.get('/reviews/page').json()['total'] == 0
    client.post('/stores/owner/activate')
    assert client.get('/reviews/page').json()['total'] == 4


def test_schedule_and_job_controls(client):
    value = client.patch('/settings/schedule', json={'enabled': True, 'reviews_hours': 2, 'products_hours': 12, 'backup_hours': 24}).json()
    assert value['enabled'] and value['reviews_hours'] == 2
    with Session() as db:
        db.add(Job(id='cancel-me', kind='reviews'))
        db.commit()
    assert client.post('/jobs/cancel-me/cancel').json()['status'] == 'cancelled'
    assert client.post('/jobs/cancel-me/retry').json()['status'] in ('queued', 'failed')


def test_prepare_all_unanswered_drafts_job(client, seeded):
    job = client.post('/jobs/drafts').json()
    result = client.get('/jobs/' + job['id']).json()
    assert result['status'] == 'completed'
    assert result['result']['processed'] == 4
    with Session() as db:
        assert len(list(db.scalars(select(Draft)))) == 4


def test_auto_replies_start_runs_now_and_can_be_paused(client, seeded):
    started = client.post('/auto-replies/start').json()
    assert started['enabled'] is True
    assert started['next_run_at'] is not None
    job = client.get('/jobs/' + started['job']['id']).json()
    assert job['status'] == 'completed'
    with Session() as db:
        schedule = setting(db, 'automation_schedule')
        assert schedule['enabled'] is True and schedule['drafts_enabled'] is True
        assert len(list(db.scalars(select(Draft)))) == 4

    paused = client.post('/auto-replies/pause').json()
    assert paused['enabled'] is False and paused['state'] == 'paused'
    assert paused['next_run_at'] is None
    with Session() as db:
        assert setting(db, 'automation_schedule')['drafts_enabled'] is False
        assert len(list(db.scalars(select(Draft)))) == 4


def test_auto_replies_pause_cancels_queued_generation(client):
    with Session() as db:
        db.add(Job(id='pause-drafts', kind='drafts', status='queued'))
        db.commit()
    paused = client.post('/auto-replies/pause').json()
    assert paused['state'] == 'paused'
    with Session() as db:
        job = db.get(Job, 'pause-drafts')
        assert job.status == 'cancelled' and job.cancel_requested is True


def test_auto_replies_process_reviews_after_sync(client, seeded, monkeypatch):
    async def sync_reviews(db, account_id, progress, cancelled):
        progress(100)
        return 0
    monkeypatch.setattr('app.main.wb.sync_reviews', sync_reviews)
    monkeypatch.setattr('app.main.wb_token', lambda db, account_id=None: 'test-token')
    with Session() as db:
        set_setting(db, 'automation_schedule', {'enabled': True, 'drafts_enabled': True, 'drafts_hours': 6})
        db.commit()
    sync = client.post('/jobs/sync/reviews').json()
    assert client.get('/jobs/' + sync['id']).json()['status'] == 'completed'
    with Session() as db:
        drafts_job = db.scalar(select(Job).where(Job.kind == 'drafts').order_by(Job.created_at.desc()))
        assert drafts_job.status == 'completed'
        assert len(list(db.scalars(select(Draft)))) == 4


def test_completed_memory_action_can_be_removed(client):
    aid = client.post('/memory/propose', json={'text': 'Не используй слово «пример»'}).json()['id']
    assert client.delete('/actions/' + aid).status_code == 400
    client.post('/actions/' + aid + '/confirm', json={})
    assert client.delete('/actions/' + aid).status_code == 200
    with Session() as db:
        assert db.get(Action, aid) is None


def test_draft_preview_attention_and_problem_products(client, seeded):
    preview = client.get('/jobs/drafts/preview').json()
    assert preview['total'] == 4 and preview['ai'] == 4
    attention = client.get('/reviews/page?attention=true').json()
    assert attention['total'] == 2
    report = client.get('/analytics').json()
    assert report['problem_products'][0]['negative'] == 2


def test_auto_drafts_can_propose_only_quality_positive_answers(client, seeded):
    with Session() as db:
        set_setting(db, 'automation_schedule', {'auto_propose': True, 'min_quality': 70, 'max_proposals': 100})
        db.commit()
    job = client.post('/jobs/drafts').json()
    result = client.get('/jobs/' + job['id']).json()
    assert result['status'] == 'completed' and result['result']['proposal_id']
    with Session() as db:
        action = db.get(Action, result['result']['proposal_id'])
        assert action.kind == 'publish'
        assert all(item['rating'] >= 4 and not item['manual'] for item in action.payload['items'])


def test_tavily_results_are_normalized(monkeypatch):
    async def request(provider, operation, **kwargs):
        assert provider == 'tavily' and operation == 'search'
        return {'results': [{'title': 'KANGAROO очиститель инструкция', 'content': 'Пенный очиститель: нанесите на поверхность и протрите.', 'url': 'https://example.com/instruction'}]}
    monkeypatch.setattr('app.integrations.decrypt_secret', lambda value: 'test-key')
    monkeypatch.setattr('app.integrations.guard.request', request)
    with Session() as db:
        db.add(Account(id='search:tavily', encrypted_token='encrypted'))
        from app.db import set_setting
        set_setting(db, 'search_provider', 'tavily')
        db.commit()
        results = asyncio.run(web_research.search_instructions(SimpleNamespace(brand='KANGAROO', name='Пенный очиститель'), db))
    assert results and results[0]['host'] == 'example.com'
