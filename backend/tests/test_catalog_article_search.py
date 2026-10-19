import pytest
from app.db import Product, Session, StoreListing


@pytest.mark.parametrize('query', ['G0020-001', 'g0020001', ' G0020 001 ', 'G0020/001'])
def test_article_search_uses_entire_catalog_in_both_endpoints(client, query):
    with Session() as db:
        db.add_all([Product(id='article-match', name='Масло', brand='GIGAS', part_number='G0020-001'),
                    Product(id='article-other', name='Другой товар', brand='GIGAS', part_number='G0020-004'),
                    Product(id='ozon:another-store:1', marketplace='ozon', name='Скрытый', part_number='G0020001')])
        db.flush()
        db.add_all([StoreListing(store_id='owner', product_id=pid) for pid in ('article-match', 'article-other')])
        db.commit()
    page = client.get('/products/page', params={'q': query, 'limit': 1}).json()
    assert page['total'] == 1 and not page['has_more']
    assert [item['id'] for item in page['items']] == ['article-match']
    assert [item['id'] for item in client.get('/products', params={'q': query}).json()] == ['article-match']


def test_article_search_treats_wildcards_literally_and_keeps_other_fields(client):
    with Session() as db:
        db.add_all([Product(id='literal', name='Лампа', brand='KOITO', category='Освещение', part_number='A%_1'),
                    Product(id='ordinary', name='Масло', brand='GIGAS', part_number='AX1')])
        db.flush()
        db.add_all([StoreListing(store_id='owner', product_id=pid) for pid in ('literal', 'ordinary')])
        db.commit()
    for query in ('A%_1', 'KOITO', 'Освещение'):
        assert [item['id'] for item in client.get('/products/page', params={'q': query}).json()['items']] == ['literal']
