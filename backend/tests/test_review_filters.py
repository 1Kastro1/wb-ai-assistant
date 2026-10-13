from datetime import datetime, timedelta, timezone

from app.db import Draft, Product, Review, Session, StoreProfile


def test_review_views_filter_before_pagination_and_count(client):
    with Session() as db:
        db.add(Product(id='filter-product', name='Товар', brand='Тест', category=''))
        db.add(StoreProfile(id='another-store', name='Другой магазин', provider='wb'))
        db.flush()
        for index in range(35):
            text = 'Обычный отзыв'
            if index == 30:
                text = 'Неприятный запах'
            elif index == 29:
                text = 'Мне понравился аромат'
            elif index == 28:
                text = 'Мне помог очиститель'
            elif index == 27:
                text = 'Отличный эффект'
            elif index == 31:
                text = 'Другой товар и неприятный запах'
            elif index == 32:
                text = 'Нет результата, сильный запах'
            db.add(Review(id=f'filter-{index:02}', wb_review_id=f'filter-{index:02}',
                          product_id='filter-product', rating=5, text=text,
                          created_at=(datetime(2026, 10, 1, tzinfo=timezone.utc) - timedelta(minutes=index)).isoformat(),
                          status='manual_review' if index == 34 else 'unanswered'))
        db.add(Review(id='foreign-review', wb_review_id='foreign-review', wb_account_id='another-store',
                      product_id='filter-product', rating=5, text='Другой товар', status='manual_review'))
        db.flush()
        db.add(Draft(id='filter-draft', review_id='filter-33', text='Черновик', original='Черновик',
                     model='safety-fallback-1.2', quality={'passed': False}))
        db.commit()

    manual = client.get('/reviews/page?view=manual&limit=1').json()
    assert manual['total'] == 2 and manual['has_more']
    assert manual['items'][0]['id'] == 'filter-33'
    page_two = client.get('/reviews/page?view=manual&limit=1&offset=1').json()
    assert page_two['total'] == 2 and not page_two['has_more']
    assert page_two['items'][0]['id'] == 'filter-34'

    assert [r['id'] for r in client.get('/reviews/page?view=wrong_item').json()['items']] == ['filter-31']
    assert [r['id'] for r in client.get('/reviews/page?view=no_effect').json()['items']] == ['filter-32']
    assert [r['id'] for r in client.get('/reviews/page?view=odor').json()['items']] == ['filter-30']
    context = client.get('/reviews/page?view=context&review_ids=filter-34,filter-03,foreign-review').json()
    assert context['total'] == 2
    assert {item['id'] for item in context['items']} == {'filter-34', 'filter-03'}
    assert client.get('/reviews/page?view=context').json()['total'] == 0
    assert client.get('/reviews/page?view=unknown').status_code == 422
