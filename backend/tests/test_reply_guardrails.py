from types import SimpleNamespace

from app.services import assess_reply
from app.safety import review_intent
from app.db import Product, Review, Session


def test_praise_cannot_receive_an_apology():
    review = SimpleNamespace(text='Приятный, ненавязчивый аромат! Ёлочки отдыхают!', rating=5)
    product = SimpleNamespace(name='Меловой ароматизатор', category='ароматизатор', facts=[])
    wrong = assess_reply(review, product, 'Здравствуйте! Нам жаль, что аромат оказался слишком слабым. Попробуйте разместить ароматизатор ближе к потоку воздуха. Спасибо за отзыв!')
    assert not wrong['passed']
    assert 'не трактовать положительный отзыв как жалобу' in wrong['issues']


def test_unverified_usage_instruction_requires_review():
    review = SimpleNamespace(text='Средство не помогло очистить салон', rating=2)
    product = SimpleNamespace(name='Очиститель салона', category='очиститель', facts=[])
    answer = assess_reply(review, product, 'Здравствуйте! Нам жаль, что очиститель не помог. Распылите средство и оставьте на 30 секунд, затем протрите тканью. Спасибо за обратную связь.', verified_facts=[])
    assert not answer['passed']
    assert 'способ применения не подтверждён сведениями о товаре' in answer['issues']


def test_mne_ponravilsya_is_praise_not_odor_complaint(client):
    text = 'Мне понравился аромат 🪔'
    assert review_intent(text) == 'POSITIVE_EXPERIENCE'
    assert review_intent('Мне помогло очистить салон') == 'POSITIVE_EXPERIENCE'
    assert review_intent('Мне подошёл товар') == 'POSITIVE_EXPERIENCE'
    assert review_intent('Мне не понравился аромат') == 'ODOR_COMPLAINT'
    product = Product(id='positive-aroma-product', name='Ароматизатор для авто', brand='TEST', category='Ароматизатор')
    review = Review(id='positive-aroma-review', wb_review_id='positive-aroma-review', product_id=product.id,
                    rating=5, text=text, risk='NORMAL')
    with Session() as db:
        db.add(product)
        db.add(review)
        db.commit()
    for revision in range(2):
        response = client.post('/reviews/positive-aroma-review/draft', json={})
        assert response.status_code == 200, response.text
        draft = response.json()
        assert draft['quality']['passed'] is True
        assert draft['quality']['intent'] == 'POSITIVE_EXPERIENCE'
        assert draft['quality']['score'] >= 96
        assert 'спасибо' in draft['text'].lower()
        assert not any(word in draft['text'].lower() for word in ('жаль', 'сожале', 'извин', 'не понравил'))
    assert assess_reply(review, product, 'Здравствуйте! Нам очень жаль, что аромат вам не понравился. Спасибо за отзыв!')['passed'] is False
