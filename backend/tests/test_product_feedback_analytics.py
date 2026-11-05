from datetime import datetime, timedelta, timezone
from app.db import Session, Product, Review


def test_product_feedback_is_scoped_to_product_store_period_and_reviews(client):
    current = datetime.now(timezone.utc)
    with Session() as db:
        db.add_all([Product(id='selected', name='Выбранный товар'), Product(id='other', name='Другой товар')])
        db.flush()
        rows = [
            ('one', 'selected', 'owner', 'wb', 1, 1, ['запах'], True),
            ('two', 'selected', 'owner', 'wb', 2, 2, ['упаков', 'запах'], False),
            ('five', 'selected', 'owner', 'wb', 5, 3, ['другое'], False),
            ('old', 'selected', 'owner', 'wb', 4, 60, ['достав'], False),
            ('another-product', 'other', 'owner', 'wb', 1, 1, ['чужая тема'], False),
            ('another-store', 'selected', 'foreign-store', 'wb', 1, 1, ['чужой магазин'], False),
            ('question', 'selected', 'owner', 'wb_question', 1, 1, ['вопрос'], False),
        ]
        for rid, pid, store, market, rating, age, topics, answered in rows:
            db.add(Review(id=rid, wb_review_id=rid, product_id=pid, wb_account_id=store,
                marketplace=market, rating=rating, text='Текст', topics=topics,
                created_at=(current-timedelta(days=age)).isoformat(), is_answered=answered))
        db.commit()
    response = client.get('/analytics', params={'product_id': 'selected', 'days': 30})
    assert response.status_code == 200
    result = response.json()
    assert result['product_id'] == 'selected' and result['period_days'] == 30
    assert result['total'] == 3 and result['negative'] == 2 and result['unanswered'] == 2
    assert result['average'] == 2.67
    assert result['rating_distribution'] == {'1': 1, '2': 1, '3': 0, '4': 0, '5': 1}
    assert result['negative_topics'] == {'запах': 2, 'упаков': 1}
    assert result['topics'] == {'запах': 2, 'упаков': 1, 'другое': 1}
    all_time = client.get('/analytics', params={'product_id': 'selected'}).json()
    assert all_time['total'] == 4 and all_time['rating_distribution']['4'] == 1
    assert all_time['average'] == 3
    other = client.get('/analytics', params={'product_id': 'other'}).json()
    assert other['total'] == 1 and other['negative_topics'] == {'чужая тема': 1}


def test_empty_product_feedback_contains_real_zero_counts(client):
    result = client.get('/analytics', params={'product_id': 'not-in-this-store', 'days': 30}).json()
    assert result['product_id'] == 'not-in-this-store' and result['period_days'] == 30
    assert result['total'] == 0 and result['average'] == 0
    assert result['negative_topics'] == {} and result['topics'] == {}
    assert result['rating_distribution'] == {str(rating): 0 for rating in range(1, 6)}
