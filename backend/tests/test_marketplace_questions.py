import asyncio

import pytest

from app.db import Account, Session
from app.integrations import ozon, yandex
from app.security import guard


@pytest.mark.parametrize('provider', ['ozon', 'yandex'])
def test_marketplace_questions_appear_in_active_store(client, monkeypatch, provider):
    created = client.post('/stores', json={'name': provider.title() + ' shop', 'provider': provider, 'client_id': '123', 'business_id': '456'})
    assert created.status_code == 200
    store_id = created.json()['id']
    assert client.post(f'/stores/{store_id}/activate').status_code == 200
    with Session() as db:
        db.add(Account(id=store_id, encrypted_token='fake-encrypted-token'))
        db.commit()
    monkeypatch.setattr('app.integrations.decrypt_secret', lambda value: 'test-token')

    calls = []
    async def request(service, operation, **kwargs):
        calls.append((service, operation, kwargs))
        if provider == 'ozon':
            return {'questions': [{'id': 'q-1', 'sku': 1234, 'text': 'Подойдёт ли товар?', 'status': 'NEW', 'created_at': '2026-10-11T10:00:00Z'}]}
        if kwargs['body']['needAnswer']:
            return {'result': {'questions': [{'questionIdentifiers': {'id': 777, 'offerId': 'SKU-1'}, 'text': 'Как использовать?', 'createdAt': '2026-10-11T10:00:00Z'}]}}
        return {'result': {'questions': [{'questionIdentifiers': {'id': 777, 'offerId': 'SKU-1'}, 'text': 'Как использовать?', 'createdAt': '2026-10-11T10:00:00Z'}]}}
    monkeypatch.setattr(guard, 'request', request)

    with Session() as db:
        store = db.get(__import__('app.db', fromlist=['StoreProfile']).StoreProfile, store_id)
        count = asyncio.run((ozon if provider == 'ozon' else yandex).sync_questions(db, store.id))
    assert count == 1
    page = client.get('/questions/page').json()
    assert page['total'] == 1
    assert page['items'][0]['marketplace'] == provider + '_question'
    assert page['items'][0]['is_answered'] is False
    assert client.get('/questions/stats').json()['unanswered'] == 1
    assert client.get('/reviews/page').json()['total'] == 0
    assert client.get('/products/page').json()['total'] == 1
    assert all(service == provider for service, _, _ in calls)


def test_yandex_question_url_allowlist_rejects_other_host():
    valid = 'https://api.partner.market.yandex.ru/v1/businesses/456/goods-questions'
    assert guard.validate('yandex', 'questions', url=valid)[0] == 'POST'
    with pytest.raises(ValueError):
        guard.validate('yandex', 'questions', url='https://other.example/v1/businesses/456/goods-questions')


def test_yandex_reviews_use_api_key_and_do_not_treat_read_as_answered(client, monkeypatch):
    store = client.post('/stores', json={'name': 'Market', 'provider': 'yandex', 'business_id': '456'}).json()
    with Session() as db:
        db.add(Account(id=store['id'], encrypted_token='fake-encrypted-token'))
        db.commit()
    monkeypatch.setattr('app.integrations.decrypt_secret', lambda value: 'test-token')
    seen = []
    async def request(service, operation, **kwargs):
        seen.append(kwargs)
        return {'result': {'feedbacks': [{'feedbackId': 123, 'createdAt': '2026-10-11T10:00:00Z', 'needReaction': False, 'identifiers': {'offerId': 'SKU-1'}, 'description': {'comment': 'Хороший товар'}, 'statistics': {'rating': 5, 'commentsCount': 0}}]}}
    monkeypatch.setattr(guard, 'request', request)
    with Session() as db:
        assert asyncio.run(yandex.sync_reviews(db, store['id'])) == 1
    assert seen[0]['headers'] == {'Api-Key': 'test-token'}
    from app.db import Review
    with Session() as db:
        review = db.get(Review, store['id'] + ':123')
        assert review.is_answered is False
