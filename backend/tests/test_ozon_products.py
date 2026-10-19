import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.db import Account, Job, Product, Review, Session, StoreProfile, setting
from app.integrations import ozon, upsert_market_question
from app.security import guard
from app.services import generate_draft, trusted_product_facts


def connected(client, monkeypatch):
    store = client.post('/stores', json={'name': 'Ozon catalog', 'provider': 'ozon', 'client_id': '123'}).json()['id']
    client.post('/stores/' + store + '/activate')
    with Session() as db:
        db.add(Account(id=store, encrypted_token='placeholder'))
        db.commit()
    monkeypatch.setattr('app.integrations.decrypt_secret', lambda _: 'test-token')
    return store


def fake_api(calls, denied=False, repeat=False):
    async def request(service, operation, **kwargs):
        assert service == 'ozon'
        assert operation not in ('publish', 'question_publish')
        calls.append((operation, kwargs['body']))
        if operation == 'products':
            return {'result': {'items': [{'product_id': 7}], 'total': 1000 if repeat else 1, 'last_id': 'cursor'}}
        if operation == 'product_info':
            return {'items': [{'id': 7, 'name': 'Фильтр NITTO 4ND-1008W', 'offer_id': 'SELLER-ID', 'sources': [{'sku': 11}, {'sku': 12}], 'primary_image': ['https://ir.ozone.ru/a.jpg']}]}
        if operation == 'product_attributes':
            return {'result': [{'id': 7, 'sku': 11, 'description_category_id': 17, 'type_id': 9, 'attributes': [{'id': 85, 'values': [{'value': 'NITTO'}]}, {'id': 7236, 'values': [{'value': '4ND-1008W'}]}, {'id': 4191, 'values': [{'value': 'Описание фильтра'}]}, {'id': 99, 'values': [{'value': '100 мм'}]}]}]}
        if operation == 'category_attributes':
            if denied: raise ValueError('HTTP 403')
            return {'result': [{'id': 99, 'name': 'Ширина'}]}
        raise AssertionError(operation)
    return request


@pytest.mark.parametrize('denied', [False, True])
def test_ozon_catalog_enriches_existing_skus_and_keeps_manual_facts(client, monkeypatch, denied):
    store = connected(client, monkeypatch)
    pid = f'ozon:{store}:12'
    manual = {'text': 'Уточнение владельца', 'source': 'Пользователь'}
    with Session() as db:
        db.add(Product(id=pid, name='12', marketplace='ozon', facts=[manual]))
        db.commit()
    calls = []
    monkeypatch.setattr(guard, 'request', fake_api(calls, denied))
    for _ in range(2):
        job = client.post('/jobs/sync/products').json()
        result = client.get('/jobs/' + job['id']).json()
        assert result['status'] == 'completed', result
        assert result['result']['processed'] == 1
        assert bool(result['result'].get('warning')) == denied
    with Session() as db:
        p = db.get(Product, pid)
        assert (p.brand, p.part_number) == ('NITTO', '4ND-1008W')
        assert manual in p.facts
        assert len([f for f in p.facts if f.get('imported_from') == 'ozon-catalog']) == 1
        fact = next(f for f in trusted_product_facts(db, p) if f.get('imported_from') == 'ozon-catalog')
        assert 'Описание фильтра' in fact['text']
        assert ('Ширина: 100 мм' in fact['text']) is not denied
        assert db.get(Product, f'ozon:{store}:11').image_url.endswith('/a.jpg')
        assert setting(db, f'ozon-product:{store}:12') == f'ozon:{store}:11'
    assert client.get('/products/page?q=4ND-1008W').json()['total'] == 2


def test_future_question_uses_catalog_sku_alias(client, monkeypatch):
    store = connected(client, monkeypatch)
    monkeypatch.setattr(guard, 'request', fake_api([]))
    with Session() as db:
        asyncio.run(ozon.sync_products(db, store))
        question = upsert_market_question(db, db.get(StoreProfile, store), 'q', 12, '', 'Как использовать?', '', False)
        assert question.product_id == f'ozon:{store}:11'
        db.commit()
    assert client.get('/products/page').json()['total'] == 1


def test_repeated_catalog_page_is_not_reported_as_success(client, monkeypatch):
    store = connected(client, monkeypatch)
    monkeypatch.setattr(guard, 'request', fake_api([], repeat=True))
    # Unknown total must not let the same cursor loop indefinitely.
    with Session() as db:
        with pytest.raises(ValueError, match='повторил'):
            asyncio.run(ozon.sync_products(db, store))


@pytest.mark.parametrize('marketplace', ['wb', 'ozon', 'yandex', 'wb_question', 'ozon_question', 'yandex_question'])
def test_all_marketplaces_generate_checked_drafts_when_model_is_unavailable(client, marketplace):
    with Session() as db:
        db.add(Product(id='product', name='Меловой ароматизатор EIKOSHA A-43', brand='EIKOSHA', category='ароматизатор', facts=[{'kind': 'feature', 'text': 'Меловой ароматизатор с ненавязчивым ароматом', 'source': 'Карточка товара', 'verification_status': 'VERIFIED'}]))
        db.flush()
        db.add(Review(id='review', wb_review_id='review', product_id='product', rating=0 if marketplace.endswith('_question') else 2, text='Как использовать?' if marketplace.endswith('_question') else 'Запах очень слабый', marketplace=marketplace))
        db.commit()
        with patch('app.services.ollama.reply_variants', new=AsyncMock(side_effect=ConnectionError())), patch('app.services.web_research.search_instructions', new=AsyncMock(return_value=[])):
            draft = asyncio.run(generate_draft(db, 'review'))
        assert draft['text'] and draft['id']
        assert db.get(Review, 'review').is_answered is False


def test_ozon_catalog_endpoints_are_read_only_and_host_bound():
    for operation in ('products', 'product_info', 'product_attributes', 'category_attributes'):
        method, url = guard.validate('ozon', operation)
        assert method == 'POST' and url.startswith('https://api-seller.ozon.ru/')
        with pytest.raises(ValueError): guard.validate('ozon', operation, url='https://other.example/')


@pytest.mark.parametrize('kind,path', [('drafts', '/jobs/drafts'), ('question_drafts', '/jobs/question-drafts')])
def test_batch_generation_does_not_reuse_another_store_job(client, monkeypatch, kind, path):
    store = connected(client, monkeypatch)
    monkeypatch.setattr('app.main.run_job_queue', AsyncMock())
    with Session() as db:
        db.add(Job(id='other-store-job', kind=kind, status='running', result={'account_id': 'owner'}))
        db.commit()
    created = client.post(path, json={}).json()
    assert created['id'] != 'other-store-job'
    assert created['result']['account_id'] == store
    assert client.post(path, json={}).json()['id'] == created['id']


def test_ozon_review_cursor_error_preserves_committed_progress(client, monkeypatch):
    store = connected(client, monkeypatch)
    saved = []
    async def request(*args, **kwargs):
        return {'reviews': [{'id': 'r', 'sku': 11, 'rating': 5, 'text': 'Спасибо'}], 'last_id': 'same', 'has_next': True}
    monkeypatch.setattr(guard, 'request', request)
    with Session() as db:
        with pytest.raises(ValueError, match='повторил'):
            asyncio.run(ozon.sync_reviews(db, store, checkpoint=saved.append))
        assert saved == [{'last_id': 'same', 'processed': 1}]
        assert db.scalar(select(Review).where(Review.wb_account_id == store)).text == 'Спасибо'


def test_interactive_generation_can_run_between_bulk_items(client, monkeypatch):
    from app import main
    with Session() as db:
        db.add(Job(id='interactive', kind='single_question_draft', status='queued', priority=10, result={'account_id': 'other-store'}))
        db.commit()
    execute = AsyncMock()
    monkeypatch.setattr(main, 'execute_job', execute)
    asyncio.run(main.run_waiting_sync())
    execute.assert_awaited_once_with('interactive')


@pytest.mark.parametrize('provider', ['ozon', 'yandex'])
def test_assortment_recommendations_use_the_selected_marketplace(client, provider):
    from app.assortment_questions import assortment_question_answer, review_vent_holder
    with Session() as db:
        db.add(StoreProfile(id='shop', provider=provider, name='Shop'))
        current = Product(id=f'{provider}:shop:100', marketplace=provider, brand='EIKOSHA', name='Меловой ароматизатор A-43')
        db.add_all([current, Product(id=f'{provider}:shop:200', marketplace=provider, brand='EIKOSHA', name='Яблочный ароматизатор A-11'), Product(id=f'{provider}:shop:300', marketplace=provider, brand='EIKOSHA', name='Держатель на дефлектор'), Product(id=f'{provider}:other:999', marketplace=provider, brand='EIKOSHA', name='Яблочный ароматизатор')])
        db.flush()
        question = Review(wb_account_id='shop', text='Есть яблочный запах?')
        result = assortment_question_answer(db, question, current, lambda *_: [])
        assert '200' in result['answer'] and '999' not in result['answer']
        assert 'Wildberries' not in result['answer']
        holder = review_vent_holder(db, question, current)
        assert '300' in holder['article_label'] and 'Wildberries' not in holder['article_label']
