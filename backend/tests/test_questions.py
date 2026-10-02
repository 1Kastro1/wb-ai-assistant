from app.db import Draft, Job, Review, Session, setting
from app.integrations import guard, ollama


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
    assert draft['quality']['intent_label'] == 'вопрос о товаре'

    action = client.post('/questions/owner:question:q-1/publish').json()
    assert action['payload']['items'][0]['kind'] == 'question'
    assert client.post('/actions/' + action['id'] + '/confirm', json={}).status_code == 200


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
    client.post('/settings/wb-token', json={'token': 'fake-token-for-question-tests'})
    for _ in range(2):
        response = client.post('/sync/questions')
        assert response.status_code == 200, response.text

    page = client.get('/questions/page').json()
    assert page['total'] == 1
    assert page['items'][0]['wb_review_id'] == 'question:remote-question'
    assert calls.count('questions') == 4
