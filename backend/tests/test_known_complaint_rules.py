import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.db import Session, Product, Review, Memory, Draft
from app.reply_policy import analyze_customer_message, has_redundant_clarification, contextual_complaint_reply
from app.owner_reply_policy import constraints, fingerprint, saved_rule_quality, enforce_owner_rules
from app.review_cases import specific_review_case
from app.services import generate_draft, assess_reply, draft_public, propose_publish

BUYER = ('Очищает хорошо, разводов практически нет. Пришло с нерабочим распылителем, '
         'он фактически развалился а также флакон протёк. Упакован отвратительно! '
         'Был завернут всего в один слой стрейча и в драный пакет. Минус звезда за '
         'упаковку и соответственно за сломанный распылитель. Поменял на новый, '
         'все работает, но осадочек остался)')
NO_REASK = 'Если покупатель уже описал проблему, не спрашивай повторно, что именно его не устроило.'
RULES = ['Каждый ответ начинай с «Добрый день!».', NO_REASK,
         'В конце ответа добавляй подпись «С уважением ГК Юником.».']
BAD = ('Добрый день! Спасибо за Ваш отзыв. Приносим извинения за то, что товар оказался '
       'повреждённым; средство протекло. Для проверки уточните, пожалуйста, детали проблемы; '
       'при необходимости обратитесь в официальную поддержку маркетплейса.\nС уважением ГК Юником.')


@pytest.mark.parametrize('marketplace', ['wb','ozon','yandex'])
def test_screenshot_reply_is_rejected_even_with_an_old_passed_model_verdict(marketplace):
    review = SimpleNamespace(text=BUYER,rating=4,risk='NORMAL',marketplace=marketplace)
    product = SimpleNamespace(name='Fine Glass, очиститель стёкол',brand='Kangaroo',category='',facts=[])
    previous = {'fingerprint':fingerprint(RULES),'checked':True,'passed':True,'issues':[]}
    assert not saved_rule_quality(BAD,RULES,previous,review=review)['passed']
    quality = assess_reply(review,product,BAD,[])
    assert not quality['passed']
    assert any('переспрашивать' in issue for issue in quality['issues'])
    case = specific_review_case(review,product)
    assert case['key'] == 'replaced_sprayer'
    for detail in ('поломку', 'протечку', 'упаковки', 'уже заменили', 'хороший результат очистки'):
        assert detail in case['answer']
    assert 'фотографии' in case['answer']
    assert 'ПВЗ' not in case['answer']
    assert assess_reply(review,product,case['answer'],[])['passed']


@pytest.mark.parametrize('rule', [NO_REASK,
    'Не проси заново описать уже названную проблему.',
    'Содержание ответа. Не проси повторно описать уже указанную проблему.'])
@pytest.mark.parametrize('clarification', ['Для проверки уточните, пожалуйста, детали проблемы.',
    'Расскажите, пожалуйста, что именно Вас не устроило?',
    'Опишите, пожалуйста, ситуацию.', 'Подскажите, в чём заключается проблема?'])
def test_local_rule_guard_recognizes_reworded_repeated_questions(rule,clarification):
    review = SimpleNamespace(text='Флакон протёк, распылитель сломан.',rating=2,marketplace='yandex')
    assert constraints([rule])['no_reask']
    assert has_redundant_clarification(clarification,review.text)
    previous={'fingerprint':fingerprint([rule]),'checked':True,'passed':True}
    assert not saved_rule_quality(clarification,[rule],previous,review=review)['passed']


@pytest.mark.parametrize('clarification', ['Пришлите фото маркировки и упаковки, если они сохранились.',
    'Уточните VIN автомобиля для проверки совместимости.',
    'Уточните материал поверхности и тип загрязнения.'])
def test_targeted_missing_information_is_not_a_generic_reask(clarification):
    assert not has_redundant_clarification(clarification,'Флакон протёк, распылитель сломан.')


def test_low_rating_without_text_may_ask_for_reason():
    assert not has_redundant_clarification('Подскажите, что именно Вас не устроило?', '')


@pytest.mark.parametrize('text', ['Флакон протёк, упаковка помята.',
    'Пришёл другой товар, не хватает деталей.', 'Товар сломан и не работает.'])
def test_fallback_addresses_known_complaints_without_retelling_request(text):
    analysis=analyze_customer_message(text,2)
    answer=contextual_complaint_reply(analysis)
    assert not has_redundant_clarification(answer,text)
    assert 'фотографии' in answer.lower()


@pytest.mark.parametrize('marketplace', ['wb','ozon','yandex'])
def test_generation_and_saved_gate_cover_mixed_review_on_every_market(client,monkeypatch,marketplace):
    from app.integrations import ollama
    judge=AsyncMock(return_value={'compliant':True,'violations':[],'revised_answer':''})
    monkeypatch.setattr(ollama,'structured',judge)
    with Session() as db:
        db.add(Product(id='glass',name='Средство для мытья окон Fine Glass',brand='Kangaroo'))
        db.flush()
        db.add(Review(id='mixed',product_id='glass',wb_review_id='mixed-remote',rating=4,text=BUYER,marketplace=marketplace))
        db.add_all([Memory(id=f'known-{i}',kind='REVIEW_RULE',text=rule) for i,rule in enumerate(RULES)])
        db.commit()
        result=asyncio.run(generate_draft(db,'mixed'))
        assert result['quality']['passed'],result['quality']
        assert result['quality']['owner_rules']['passed']
        assert 'уже заменили' in result['text']
        assert not has_redundant_clarification(result['text'],BUYER)
        draft=db.get(Draft,result['id'])
        # A misleading legacy approval must not survive display/publication checks.
        draft.text=BAD
        db.commit()
        assert not draft_public(db,draft)['quality']['owner_rules']['passed']
        with pytest.raises(ValueError,match='правила магазина'):
            propose_publish(db,['mixed'])
