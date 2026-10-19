import asyncio

import pytest

from app import main
from app.db import Account, Job, Product, Review, Session
from app.integrations import yandex
from app.security import guard


def connected_store(client, monkeypatch):
    store = client.post('/stores', json={'name': 'Market catalog', 'provider': 'yandex', 'business_id': '456'}).json()
    client.post('/stores/' + store['id'] + '/activate')
    with Session() as db:
        db.add(Account(id=store['id'], encrypted_token='test-placeholder'))
        db.commit()
    monkeypatch.setattr('app.integrations.decrypt_secret', lambda value: 'private-test-token')
    return store['id']


def page(sku, next_page=''):
    return {'status': 'OK', 'result': {'offerMappings': [{
        'offer': {'offerId': sku, 'name': 'Масляный фильтр ' + sku, 'vendor': 'BRAND',
                  'vendorCode': 'PART-' + sku, 'pictures': ['https://example.com/' + sku + '.jpg'],
                  'description': 'Описание фильтра', 'manufacturerCountries': ['Япония'],
                  'weightDimensions': {'weight': 0.2, 'length': 5, 'width': 6, 'height': 7}},
        'mapping': {'marketCategoryName': 'Масляные фильтры'},
    }], 'paging': {'nextPageToken': next_page} if next_page else {}}}


def test_catalog_sync_updates_review_placeholders_and_imports_products_without_reviews(client, monkeypatch):
    store = connected_store(client, monkeypatch)
    pid = 'yandex:' + store + ':SKU-1'
    manual = {'text': 'Важное уточнение пользователя', 'source': 'Пользователь'}
    with Session() as db:
        db.add(Product(id=pid, name='SKU-1', marketplace='yandex', facts=[manual]))
        db.flush()
        db.add(Review(id='old-review', wb_review_id='old-review', wb_account_id=store, product_id=pid, rating=5, text='Хорошо', marketplace='yandex'))
        db.add(Product(id='yandex:another-store:SKU-1', name='Чужой товар', marketplace='yandex'))
        db.commit()
    calls = []

    async def request(service, operation, **kwargs):
        assert (service, operation) == ('yandex', 'products')
        assert kwargs['url'].endswith('/businesses/456/offer-mappings')
        assert kwargs.get('body') is None
        assert kwargs['params']['limit'] == 100
        calls.append(kwargs['params'].get('pageToken', ''))
        return page('SKU-2') if calls[-1] else page('SKU-1', 'next')

    monkeypatch.setattr(guard, 'request', request)
    for _ in range(2):
        job = client.post('/jobs/sync/products').json()
        result = client.get('/jobs/' + job['id']).json()
        assert result['status'] == 'completed', result
        assert result['result']['processed'] == 2
    assert calls == ['', 'next', '', 'next']
    with Session() as db:
        product = db.get(Product, pid)
        assert product.name == 'Масляный фильтр SKU-1'
        assert (product.brand, product.part_number, product.category) == ('BRAND', 'PART-SKU-1', 'Масляные фильтры')
        assert product.image_url == 'https://example.com/SKU-1.jpg'
        assert manual in product.facts
        assert len([fact for fact in product.facts if fact.get('imported_from') == 'yandex-catalog']) == 2
        assert db.get(Review, 'old-review').product_id == pid
        assert db.get(Product, 'yandex:another-store:SKU-1').name == 'Чужой товар'
    assert client.get('/products/page').json()['total'] == 2
    assert client.get('/reviews/page').json()['total'] == 1


def test_catalog_resumes_after_committed_page(client, monkeypatch):
    store = connected_store(client, monkeypatch)
    calls, saved = [], []
    fail = True

    async def request(*args, **kwargs):
        nonlocal fail
        cursor = kwargs['params'].get('pageToken', '')
        calls.append(cursor)
        if cursor and fail:
            fail = False
            raise ValueError('Temporary network error')
        return page('SKU-2') if cursor else page('SKU-1', 'next')

    monkeypatch.setattr(guard, 'request', request)
    with Session() as db:
        with pytest.raises(ValueError, match='Temporary'):
            asyncio.run(yandex.sync_products(db, store, checkpoint=saved.append))
    assert saved[-1] == {'page': 'next', 'processed': 1}
    with Session() as db:
        assert asyncio.run(yandex.sync_products(db, store, resume=saved[-1])) == 2
    assert calls == ['', 'next', 'next']
    assert client.get('/products/page').json()['total'] == 2


def test_catalog_permission_error_is_actionable_and_not_a_fake_success(client, monkeypatch):
    store = connected_store(client, monkeypatch)

    async def forbidden(*args, **kwargs):
        raise ValueError('yandex: HTTP 403. Forbidden')

    monkeypatch.setattr(guard, 'request', forbidden)
    with Session() as db:
        db.add(Job(id='forbidden-catalog', kind='products', max_attempts=1, result={'account_id': store}))
        db.commit()
    asyncio.run(main.run_sync_job('forbidden-catalog'))
    result = client.get('/jobs/forbidden-catalog').json()
    assert result['status'] == 'failed'
    assert 'Просмотр товаров и карточек' in result['result']['message']
    assert 'Business ID' in result['result']['message']
    assert 'private-test-token' not in str(result)


def test_repeated_catalog_page_stops_without_looping(client, monkeypatch):
    store = connected_store(client, monkeypatch)
    calls = []

    async def request(*args, **kwargs):
        calls.append(kwargs['params'].get('pageToken', ''))
        return page('SKU-1', 'repeated')

    monkeypatch.setattr(guard, 'request', request)
    with Session() as db:
        with pytest.raises(ValueError, match='повторную страницу'):
            asyncio.run(yandex.sync_products(db, store))
    assert len(calls) == 2


def test_catalog_read_endpoint_does_not_allow_update_or_other_business_urls():
    url = 'https://api.partner.market.yandex.ru/v2/businesses/456/offer-mappings'
    assert guard.validate('yandex', 'products', url=url)[0] == 'POST'
    for invalid in (url + '/update', url.replace('api.partner.market.yandex.ru', 'other.example'), url + '?token=secret'):
        with pytest.raises(ValueError):
            guard.validate('yandex', 'products', url=invalid)


def test_historical_placeholder_missing_from_catalog_is_recovered_by_exact_sku(client, monkeypatch):
    store = connected_store(client, monkeypatch)
    manual = {'text': 'Правка сотрудника', 'source': 'Пользователь'}
    with Session() as db:
        db.add_all([
            Product(id=f'yandex:{store}:101482', name='101482', marketplace='yandex', facts=[manual]),
            Product(id='yandex:another:101482', name='101482', marketplace='yandex'),
        ])
        db.flush()
        db.add(Review(id='historical', wb_review_id='historical', wb_account_id=store,
            product_id=f'yandex:{store}:101482', marketplace='yandex', rating=5, text='Хорошо'))
        db.commit()
    calls = []
    async def request(service, operation, **kwargs):
        assert (service, operation) == ('yandex', 'products')
        calls.append(kwargs)
        if kwargs.get('body'):
            assert kwargs['body'] == {'offerIds': ['101482']}
            assert kwargs['params'] == {}  # Exact SKU lookup forbids pagination filters.
            return page('101482')
        return page('current')
    monkeypatch.setattr(guard, 'request', request)
    with Session() as db:
        assert asyncio.run(yandex.sync_products(db, store)) == 2
        product = db.get(Product, f'yandex:{store}:101482')
        assert product.name == 'Масляный фильтр 101482'
        assert product.brand == 'BRAND' and product.part_number == 'PART-101482'
        assert manual in product.facts
        assert db.get(Review, 'historical').product_id == product.id
        assert db.get(Product, 'yandex:another:101482').brand == ''
        assert asyncio.run(yandex.sync_products(db, store)) == 1
    assert len(calls) == 3  # No repeat lookup after the placeholder is resolved.


def test_missing_historical_offer_stays_unconfirmed_without_inventing_data(client, monkeypatch):
    store = connected_store(client, monkeypatch)
    with Session() as db:
        db.add(Product(id=f'yandex:{store}:gone', name='gone', marketplace='yandex'))
        db.commit()
    async def request(*args, **kwargs):
        if kwargs.get('body'): return {'status': 'OK', 'result': {'offerMappings': []}}
        return page('current')
    monkeypatch.setattr(guard, 'request', request)
    with Session() as db:
        assert asyncio.run(yandex.sync_products(db, store)) == 1
        product = db.get(Product, f'yandex:{store}:gone')
        assert product.name == 'gone' and not product.facts and not product.brand
