import asyncio
from datetime import datetime

from app.db import Draft, Job, Product, Review, Session, setting
from app.integrations import guard, ollama, wb_question_pages
from app.services import assess_question_answer


def add_question():
    with Session() as db:
        db.add(Review(
            id='owner:question:q-1',
            wb_account_id='owner',
            wb_review_id='question:q-1',
            product_id='p1',
            rating=0,
            text='Подойдёт ли этот товар для ежедневного использования?',
            marketplace='wb_question',
        ))
        db.commit()


def test_battery_answer_distinguishes_nominal_voltage_from_measured_charge():
    product = Product(id='battery', name='Аккумулятор автомобильный 12 В', brand='DOMEI')
    question = Review(id='battery-question', wb_review_id='battery-question', product_id='battery',
                      rating=0, text='Сколько вольт должно быть на клеммах при покупке?',
                      marketplace='wb_question')
    sources = [{'text': 'Номинальное напряжение 12 В', 'verification_status': 'VERIFIED'}]
    inaccurate = assess_question_answer(question, product,
        'Добрый день! На клеммах аккумулятора при покупке должно быть 12 В.', sources)
    careful = assess_question_answer(question, product,
        'Добрый день! По карточке номинальное напряжение аккумулятора — 12 В. Фактическое напряжение заряда на клеммах при покупке в карточке не указано.', sources)
    assert inaccurate['passed'] is False
    assert 'номинальное' in inaccurate['issues'][0]
    assert careful['passed'] is True


def test_wb_question_pagination_rolls_date_window_before_offset_limit(monkeypatch):
    rows = [
        {'id': str(second), 'createdDate': f'2026-10-02T10:00:{second:02d}Z'}
        for second in range(6, 0, -1)
    ]
    calls = []

    async def request(provider, operation, **kwargs):
        assert (provider, operation) == ('wb', 'questions')
        params = kwargs['params']
        assert params['take'] + params['skip'] <= 4
        calls.append(params)
        visible = [row for row in rows if 'dateTo' not in params or
                   int(datetime.fromisoformat(row['createdDate'].replace('Z', '+00:00')).timestamp()) <= params['dateTo']]
        return {'data': {'questions': visible[params['skip']:params['skip'] + params['take']]}}

    monkeypatch.setattr(guard, 'request', request)

    async def collect():
        return [item['id'] async for page in wb_question_pages('token', False, page_size=2, window_size=4)
                for item in page]

    assert asyncio.run(collect()) == ['6', '5', '4', '3', '2', '1']
    assert any('dateTo' in params for params in calls)


def test_questions_are_separate_and_have_their_own_draft_flow(client, seeded, monkeypatch):
    add_question()

    async def variants(*args, **kwargs):
        return [
            'Здравствуйте! Товар подходит для ежедневного использования. Следуйте инструкции в карточке товара.',
            'Здравствуйте! Спасибо за вопрос. Используйте товар по инструкции, указанной в карточке.',
            'Добрый день! Для ежедневного использования соблюдайте рекомендации из карточки товара.',
        ]

    monkeypatch.setattr(ollama, 'reply_variants', variants)
    questions = client.get('/questions/page?unanswered=true').json()
    assert questions['total'] == 1
    assert questions['items'][0]['id'] == 'owner:question:q-1'
    assert all(row['id'] != 'owner:question:q-1' for row in client.get('/reviews').json())

    response = client.post('/questions/owner:question:q-1/draft', json={})
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft['text'].startswith(('Здравствуйте', 'Добрый день'))
    assert draft['quality']['intent_label'] == 'совместимость'
    assert draft['quality']['score'] >= 90

    action = client.post('/questions/owner:question:q-1/publish').json()
    assert action['payload']['items'][0]['kind'] == 'question'
    assert client.post('/actions/' + action['id'] + '/confirm', json={}).status_code == 200


def test_single_draft_job_creates_and_regenerates_question_answer(client, seeded, monkeypatch):
    add_question()

    async def variants(*args, **kwargs):
        return ['Здравствуйте! Спасибо за вопрос. Следуйте инструкции в карточке товара.']

    monkeypatch.setattr(ollama, 'reply_variants', variants)
    for instruction in ('', 'Ответьте чуть короче'):
        response = client.post('/jobs/single-draft', json={
            'review_id': 'owner:question:q-1', 'instruction': instruction,
        })
        assert response.status_code == 200, response.text
        job = client.get('/jobs/' + response.json()['id']).json()
        assert job['kind'] == 'single_question_draft'
        assert job['status'] == 'completed', job['result']
        assert job['result']['draft']['review_id'] == 'owner:question:q-1'
    with Session() as db:
        assert db.query(Draft).filter_by(review_id='owner:question:q-1').count() == 1


def test_assistant_toggle_stops_generation_chat_and_queued_drafts(client, seeded):
    with Session() as db:
        db.add(Job(id='queued-drafts', kind='drafts', status='queued'))
        db.commit()

    disabled = client.patch('/settings/assistant', json={'enabled': False})
    assert disabled.status_code == 200 and disabled.json()['enabled'] is False
    assert client.post('/reviews/r3/draft', json={}).status_code == 400
    assert client.post('/chat', json={'text': 'Что требует внимания?'}).status_code == 400
    with Session() as db:
        assert db.get(Job, 'queued-drafts').status == 'cancelled'
        assert setting(db, 'automation_schedule', {}).get('drafts_enabled') is False

    assert client.patch('/settings/assistant', json={'enabled': True}).json()['enabled'] is True


def test_wb_questions_sync_is_idempotent(client, monkeypatch):
    calls = []

    async def request(provider, operation, **kwargs):
        calls.append(operation)
        if operation == 'questions' and kwargs['params']['isAnswered'] == 'false':
            return {'data': {'questions': [{
                'id': 'remote-question',
                'text': 'Как пользоваться товаром?',
                'createdDate': '2026-10-02T10:00:00Z',
                'productDetails': {'nmId': 77, 'productName': 'Очиститель', 'brandName': 'Kangaroo'},
            }]}}
        if operation == 'questions':
            return {'data': {'questions': []}}
        raise AssertionError('unexpected operation: ' + operation)

    monkeypatch.setattr(guard, 'request', request)
    saved = client.post('/settings/wb-token', json={'token': 'fake-token-for-question-tests-with-safe-length-1234567890'})
    assert saved.status_code == 200, saved.text
    for _ in range(2):
        response = client.post('/sync/questions')
        assert response.status_code == 200, response.text

    page = client.get('/questions/page').json()
    assert page['total'] == 1
    assert page['items'][0]['wb_review_id'] == 'question:remote-question'
    assert calls.count('questions') == 4


def test_question_stats_filters_and_batch_job(client, seeded, monkeypatch):
    async def variants(*args, **kwargs):
        return [
            'Здравствуйте! Да, товар подходит для ежедневного использования при соблюдении инструкции.',
            'Добрый день! Товар можно использовать ежедневно по инструкции из карточки товара.',
            'Здравствуйте! Для ежедневного применения соблюдайте рекомендации производителя.',
        ]
    monkeypatch.setattr(ollama, 'reply_variants', variants)
    add_question()
    stats = client.get('/questions/stats').json()
    assert stats['total'] == 1
    assert stats['unanswered'] == 1
    assert stats['types']['совместимость'] == 1

    page = client.get('/questions/page?kind=совместимость&limit=1').json()
    assert page['total'] == 1
    assert page['items'][0]['question_type'] == 'совместимость'
    assert client.get('/questions/page?kind=применение').json()['total'] == 0

    response = client.post('/jobs/question-drafts', json={'question_ids': ['owner:question:q-1']})
    assert response.status_code == 200, response.text
    assert response.json()['kind'] == 'question_drafts'
