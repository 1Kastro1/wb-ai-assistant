import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.reply_policy import conversational_issues, semantic_reply_issues
from app.lamp_specifications import lamp_specifications
from app.review_cases import specific_review_case
from app.safety import safe_fallback_reply


def review(text, rating=4):
    return SimpleNamespace(text=text, rating=rating, risk='NORMAL')


def product(name='Лампы H1 12V 55W (120W) 5000K Avantech AB5001', brand='Avantech', facts=None):
    return SimpleNamespace(name=name, brand=brand, category='', facts=facts or [])


def test_power_reply_answers_from_exact_card_and_series_instead_of_refusing():
    buyer = review('работают, упаковано было хорошо. поменял в дальнем, разницу не заметил, может кто нагрузку мерял? может там и нет 55?')
    case = specific_review_case(buyer, product())
    assert '55 Вт при 12 В' in case['answer']
    assert '5000 К' in case['answer'] and 'оттенок' in case['answer']
    assert 'заявлена' in case['answer'] and 'Жаль' in case['answer']
    assert not conversational_issues(case['answer'])
    assert any(s['source_type'] == 'manufacturer' for s in case['sources'])
    assert not semantic_reply_issues(buyer, product(), case['answer'])


@pytest.mark.parametrize('name,brand', [
    ('Лампы AB50010 H1 12V 55W', 'Avantech'),
    ('Лампы AB5001 H1 12V 55W', 'Other'),
    ('Набор AB5001 AB5007 H1 12V 55W', 'Avantech'),
    ('Лампы Avantech AB5001', 'Avantech'),
])
def test_declared_power_is_not_transferred_to_another_or_incomplete_card(name, brand):
    spec = lamp_specifications(product(name, brand))
    assert spec['power'] is None


def test_other_lamps_use_labelled_verified_card_specs_not_marketing_equivalence():
    p = product('Лампы другой модели', 'Other', [
        {'text':'Мощность: 60 Вт\nНапряжение: 12 В', 'source':'Карточка товара', 'verification_status':'VERIFIED'},
        {'text':'Мощность: 120 Вт', 'source':'https://random.example', 'verification_status':'VERIFIED'},
    ])
    result = specific_review_case(review('Какая мощность у ламп?'), p)
    assert '60 Вт при 12 В' in result['answer']
    assert '55' not in result['answer'] and '5000' not in result['answer'] and '120' not in result['answer']


@pytest.mark.parametrize('message,rating,name', [
    ('Отличный фильтр, установился без проблем',5,'Фильтр Nitto'),
    ('Светят норм, через месяц перестали работать',3,'LED лампы'),
    ('Упаковка повреждена',2,'Фильтр Nitto'),
    ('Не использовал, проверю позже',5,'Щётки стеклоочистителя'),
    ('Работают, но разницу не заметил. Какая мощность?',4,'Лампы неизвестной модели'),
])
def test_warm_fallbacks_are_shared_across_products_and_review_sentiments(message,rating,name):
    result = safe_fallback_reply(review(message,rating), product(name))
    assert 'Спасибо' in result
    assert not conversational_issues(result)
    assert 'Приносим извинения' not in result and 'доставленных неудобствах' not in result


@pytest.mark.parametrize('answer', [
    'Здравствуйте! Не будем утверждать значение, которое не можем подтвердить.',
    'Для ответа нужны проверенные данные измерений.',
    'По доступным подтверждённым сведениям информации нет.',
])
def test_internal_disclaimers_fail_content_check_for_every_product(answer):
    for p in (product(), product('Фильтр'), product('Лопата')):
        assert semantic_reply_issues(review('Есть вопрос о товаре?'),p,answer)


def test_useful_uncertainty_and_safety_warning_are_preserved():
    assert not conversational_issues('По описанию нельзя определить причину. При дыме прекратите использование и обратитесь в сервис.')


def test_dry_verifier_rewrite_is_repaired_without_reusing_its_approval(client,monkeypatch):
    from app import services
    from app.db import Session, Product, Review, Memory
    async def dry(*args,**kwargs):
        return 'Здравствуйте! Не будем утверждать значение. С уважением ГК Юником.', {'passed':True,'checked':True,'issues':[]}
    monkeypatch.setattr(services,'enforce_owner_rules',dry)
    monkeypatch.setattr(services.ollama,'reply_variants',AsyncMock(side_effect=TimeoutError()))
    with Session() as db:
        p=Product(id='human-lamp',name=product().name,brand='Avantech')
        r=Review(id='human-review',wb_review_id='human-remote',product_id=p.id,wb_account_id='owner',
                 rating=4,text='Работают. Разницу не заметил, может кто нагрузку мерял?',marketplace='yandex',risk='NORMAL')
        db.add_all([p,r,Memory(id='human-sign',kind='REVIEW_RULE',scope='global',text='В конце ответа добавляй подпись «С уважением ГК Юником.».')])
        db.commit()
        draft=asyncio.run(services.generate_draft(db,r.id))
        assert '55 Вт' in draft['text'] and not conversational_issues(draft['text'])
        assert not draft['quality']['owner_rules']['checked']
