from types import SimpleNamespace
import pytest
from app.reply_policy import analyze_customer_message, semantic_reply_issues
from app.review_cases import specific_review_case
from app.safety import safe_fallback_reply

PRAISE = 'Нет.\nВсё в порядке, электролит не протёк.\nДоставка выполнена на отлично.\nЗаряжен на 90%, в остальном время покажет.\nПродавцу отлично.'


def objects(text, name='Аккумулятор автомобильный DOMEI', rating=5, market='yandex'):
    return (SimpleNamespace(text=text, rating=rating, risk='NORMAL', marketplace=market),
            SimpleNamespace(name=name, category='', brand='DOMEI', facts=[]))


@pytest.mark.parametrize('market', ['wb', 'ozon', 'yandex'])
def test_exact_battery_praise_never_becomes_damage_or_leakage(market):
    review, product = objects(PRAISE, market=market)
    analysis = analyze_customer_message(review.text, review.rating)
    assert analysis['sentiment'] == 'positive' and not analysis['complaints']
    assert specific_review_case(review, product) is None
    reply = safe_fallback_reply(review, product).lower()
    assert 'спасибо' in reply or 'благодар' in reply
    assert not any(word in reply for word in ('извин', 'сожале', 'протеч', 'поврежд', 'возврат'))
    assert not semantic_reply_issues(review, product, reply)


@pytest.mark.parametrize('text', [
    'Электролит не протёк. Всё отлично.', 'Масло не вытекло. Отличный товар.',
    'Протечек нет, повреждений нет. Рекомендую.', 'Нет никаких протечек, всё хорошо.',
    'Не было трещин. Всё отлично.', 'Без трещин и дефектов. Отлично.',
    'Упаковка не повреждена. Хороший товар.', 'Колодки не скрипят и не свистят. Всё отлично.',
])
def test_explicit_absence_of_faults_is_not_a_complaint(text):
    review, product = objects(text, name='Колодки тормозные' if 'Колодки' in text else 'Товар')
    assert not analyze_customer_message(text, 5)['complaints']
    assert specific_review_case(review, product) is None


@pytest.mark.parametrize('text', ['Электролит протёк.', 'Масло вытекло.', 'Не только протекло, но и разбилось.'])
def test_real_leakage_stays_a_complaint(text):
    assert 'LEAKAGE' in analyze_customer_message(text, 1)['complaints']


def test_denial_in_one_clause_cannot_hide_real_leakage_in_another():
    assert 'LEAKAGE' in analyze_customer_message('Масло не протекло, но средство вытекло.', 2)['complaints']


@pytest.mark.parametrize('text', ['Без дефектов не обошлось.', 'Не без дефектов.'])
def test_double_negation_does_not_hide_real_defects(text):
    assert 'QUALITY_ISSUE' in analyze_customer_message(text, 5)['complaints']


def test_leakage_does_not_invent_packaging_damage():
    review, product = objects('Электролит протёк.', rating=1)
    answer = specific_review_case(review, product)['answer']
    assert 'протечку электролита' in answer
    assert 'повреждение упаковки' not in answer
    assert not semantic_reply_issues(review, product, answer)


def test_guard_rejects_invented_faults_even_when_rule_checker_fails():
    review, product = objects(PRAISE)
    issues = semantic_reply_issues(review, product,
        'Добрый день! Приносим извинения за повреждение упаковки и протечку электролита.')
    assert 'не трактовать положительный отзыв как жалобу' in issues
    assert 'не придумывать повреждение упаковки' in issues
    assert 'не извиняться за неописанную протечку' in issues


@pytest.mark.parametrize('market', ['wb', 'ozon', 'yandex'])
def test_generated_battery_answer_is_positive_even_when_model_is_offline(client, market, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from app import services
    from app.db import Session, StoreProfile, Product, Review
    monkeypatch.setattr(services.ollama, 'reply_variants', AsyncMock(side_effect=ValueError('offline')))
    monkeypatch.setattr(services.ollama, 'chat', AsyncMock(side_effect=ValueError('offline')))
    monkeypatch.setattr(services.web_research, 'search_instructions', AsyncMock(return_value=[]))
    with Session() as db:
        db.add(StoreProfile(id='battery-'+market, name='Test', provider=market))
        db.add(Product(id='battery-product', name='Аккумулятор автомобильный DOMEI', brand='DOMEI'))
        db.flush()
        db.add(Review(id='battery-review', wb_review_id='battery', wb_account_id='battery-'+market,
                      product_id='battery-product', rating=5, text=PRAISE, marketplace=market, risk='NORMAL'))
        db.commit()
        result = asyncio.run(services.generate_draft(db, 'battery-review', 'Улучши ответ'))
        answer = result['text'].lower()
        assert not any(word in answer for word in ('извин', 'сожале', 'протеч', 'поврежд', 'возврат'))
        assert result['quality']['passed'], result['quality']['issues']
