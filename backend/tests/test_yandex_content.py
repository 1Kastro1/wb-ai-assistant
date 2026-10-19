import asyncio
import pytest
from app.db import Product, Session
from app.integrations import yandex
from app.security import guard
from app.yandex_content import parameter_lines
from test_yandex_products import connected_store, page


SCHEMA = [
    {'id': 1, 'name': 'Мощность', 'type': 'NUMERIC', 'unit': {'defaultUnitId': 7, 'units': [{'id': 7, 'name': 'Вт'}]}},
    {'id': 2, 'name': 'Цоколь', 'values': [{'id': 9, 'value': 'H7'}]},
    {'id': 3, 'name': 'Галогенная', 'type': 'BOOLEAN'},
]


def test_values_resolve_names_enums_units_and_false_without_guessing():
    assert parameter_lines([
        {'parameterId': 1, 'value': 55}, {'parameterId': 2, 'valueId': 9},
        {'parameterId': 3, 'value': False}, {'parameterId': 99, 'value': 'unknown'},
        {'parameterId': 1, 'unitId': 999, 'value': 2}, {'parameterId': 2, 'valueId': 404},
    ], SCHEMA) == ['Мощность: 55 Вт', 'Цоколь: H7', 'Галогенная: Нет']


def test_reviewed_internet_series_data_does_not_apply_to_other_models_or_brands():
    from app.reviewed_knowledge import matching_knowledge
    product = Product(id='snow', brand='Avantech', name='Щетка стеклоочистителя Snowguard 600 мм', part_number='', facts=[])
    entries = matching_knowledge(product)
    assert len(entries) == 1
    assert entries[0]['scope'] == 'brand_category'
    assert entries[0]['url'] == 'https://avantechparts.co.kr/ru/catalog/wipers'
    product.name = 'Щетка стеклоочистителя Hybrid 600 мм'
    assert matching_knowledge(product) == []
    product.name = 'Щетка стеклоочистителя Snowguard 600 мм'
    product.brand = 'OTHER'
    assert matching_knowledge(product) == []


def test_full_sync_enriches_each_page_caches_category_and_preserves_manual_facts(client, monkeypatch):
    store = connected_store(client, monkeypatch)
    calls = []
    async def request(service, operation, **kwargs):
        if kwargs.get('body') == {'archived': True}: return {'status': 'OK', 'result': {'offerMappings': []}}
        calls.append(operation)
        if operation == 'products':
            result = page('SKU-1')
            result['result']['offerMappings'][0]['mapping']['marketCategoryId'] = 123
            return result
        if operation == 'product_cards':
            assert kwargs['body'] == {'offerIds': ['SKU-1']}
            return {'status': 'OK', 'result': {'offerCards': [
                {'offerId': 'SKU-1', 'parameterValues': [{'parameterId': 1, 'value': '55'}]},
                {'offerId': 'foreign', 'parameterValues': [{'parameterId': 1, 'value': '99'}]},
            ]}}
        assert operation == 'category_parameters'
        assert kwargs['url'].endswith('/category/123/parameters')
        return {'result': {'parameters': SCHEMA}}
    monkeypatch.setattr(guard, 'request', request)
    with Session() as db:
        for _ in range(2):
            assert asyncio.run(yandex.sync_products(db, store)) == 1
        product = db.get(Product, f'yandex:{store}:SKU-1')
        facts = [f for f in product.facts if f.get('imported_from') == 'yandex-parameters']
        assert len(facts) == 1
        assert facts[0]['text'] == 'Мощность: 55 Вт'
        assert facts[0]['verification_status'] == 'VERIFIED'
        assert db.get(Product, f'yandex:{store}:foreign') is None
    assert calls.count('product_cards') == 2


def test_detailed_read_operations_cannot_be_redirected_to_writes_or_other_operations():
    root = 'https://api.partner.market.yandex.ru/v2/'
    for operation, path in [('product_cards', 'businesses/456/offer-cards'), ('category_parameters', 'category/123/parameters')]:
        assert guard.validate('yandex', operation, root + path)[0] == 'POST'
        for suffix in ('/update', '?token=secret'):
            with pytest.raises(ValueError): guard.validate('yandex', operation, root + path + suffix)
    with pytest.raises(ValueError): guard.validate('yandex', 'products', root + 'businesses/456/offer-cards')


@pytest.mark.parametrize('status', [403, 500])
@pytest.mark.parametrize('unavailable', ['category_parameters', 'product_cards'])
def test_unavailable_category_dictionary_does_not_block_catalog_or_erase_old_facts(client, monkeypatch, status, unavailable):
    store = connected_store(client, monkeypatch)
    old = {'text': 'Мощность: 55 Вт', 'kind': 'feature', 'imported_from': 'yandex-parameters',
           'source': 'Карточка товара', 'verification_status': 'VERIFIED'}
    with Session() as db:
        db.add(Product(id=f'yandex:{store}:SKU-1', name='SKU-1', marketplace='yandex', facts=[old]))
        db.commit()
    schemas = []
    async def request(service, operation, **kwargs):
        if operation == 'products':
            if kwargs.get('body') == {'archived': True}: return {'result': {'offerMappings': []}}
            result = page('SKU-1')
            result['result']['offerMappings'] += page('SKU-2')['result']['offerMappings']
            for item in result['result']['offerMappings']: item['mapping']['marketCategoryId'] = 123
            return result
        if operation == 'product_cards':
            if unavailable == 'product_cards': raise ValueError(f'yandex: HTTP {status}')
            return {'result': {'offerCards': [{'offerId': sku, 'parameterValues': [{'parameterId': 1, 'value': '99'}]} for sku in ('SKU-1', 'SKU-2')]}}
        assert operation == 'category_parameters'
        schemas.append(kwargs['url'])
        raise ValueError(f'yandex: HTTP {status}')
    monkeypatch.setattr(guard, 'request', request)
    job = client.post('/jobs/sync/products').json()
    result = client.get('/jobs/' + job['id']).json()
    assert result['status'] == 'completed', result
    assert result['result']['processed'] == 2
    assert ('123' if unavailable == 'category_parameters' else 'SKU-1') in result['result']['warning']
    assert len(schemas) == (1 if unavailable == 'category_parameters' else 0)
    with Session() as db:
        assert old in db.get(Product, f'yandex:{store}:SKU-1').facts
        product = db.get(Product, f'yandex:{store}:SKU-2')
        assert product.name == 'Масляный фильтр SKU-2'
        assert not any('99' in fact.get('text', '') for fact in product.facts)


def test_research_still_finds_details_when_instruction_already_exists(client, seeded, monkeypatch):
    from app import main
    with Session() as db:
        p = db.get(Product, 'p1')
        p.facts = [{'text': 'Проверенная инструкция', 'kind': 'instruction', 'source': 'Карточка товара', 'verification_status': 'VERIFIED'}]
        db.commit()
    topics = []
    async def search(product, db, topic):
        topics.append(topic)
        if len(topics) == 1: raise ValueError('offline')
        return [{'url': 'https://manufacturer.example/details', 'snippet': 'Характеристики', 'title': product.name}]
    monkeypatch.setattr(main.web_research, 'search_instructions', search)
    result = client.post('/products/p1/research').json()
    assert len(topics) == 2
    assert result['verified_instructions'] is True
    assert result['found'] == 1
    assert result['message']
    assert result['product']['facts'][-1]['kind'] == 'feature'
    assert result['product']['facts'][-1]['verification_status'] == 'WEB_UNVERIFIED'


def test_feature_search_candidates_do_not_become_cached_instructions():
    from app.services import cached_web_instruction_sources, remember_web_instruction_sources
    p = Product(id='cache', facts=[])
    remember_web_instruction_sources(p, [{'url': 'https://example.com/spec', 'snippet': 'Размеры', 'kind': 'feature'}])
    assert cached_web_instruction_sources(p) == []
    remember_web_instruction_sources(p, [{'url': 'https://example.com/guide', 'snippet': 'Применение', 'kind': 'instruction'}])
    assert [x['url'] for x in cached_web_instruction_sources(p)] == ['https://example.com/guide']
    assert len(p.facts) == 2


def test_exact_article_search_accepts_foreign_language_sources_and_rejects_neighbor_articles():
    from app.integrations import web_research
    p = Product(brand='GIGAS', part_number='G0020-001', name='Масло моторное синтетическое для автомобиля 1 литр')
    results = web_research._normalized_results(p, [
        {'url': 'https://gigas.example/G0020-001', 'title': 'GIGAS G0020 001 engine oil', 'snippet': 'Specifications'},
        {'url': 'https://gigas.example/G0020-0010', 'title': 'GIGAS G0020-0010', 'snippet': 'Масло моторное синтетическое для автомобиля'},
        {'url': 'https://other.example/G0020-001', 'title': 'OTHER G0020-001', 'snippet': 'Engine oil'},
    ])
    assert len(results) == 1
    assert results[0]['url'] == 'https://gigas.example/G0020-001'
