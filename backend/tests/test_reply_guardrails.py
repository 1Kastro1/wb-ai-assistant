from types import SimpleNamespace

from app.services import assess_reply


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
