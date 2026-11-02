import asyncio
from types import SimpleNamespace as S
from unittest.mock import AsyncMock
import pytest
from app import owner_reply_policy as policy
from app.rule_execution import positive_template

ANONYMOUS='Если имя покупателя скрыто или отсутствует, начинай ответ с «Здравствуйте.». '
RULES=['Каждый ответ начинай с «Добрый день!».',ANONYMOUS]


@pytest.mark.parametrize('market',['wb','ozon','yandex','wb_question','ozon_question','yandex_question'])
@pytest.mark.parametrize('name',['','Имя скрыто','Пользователь Ozon','***'])
def test_anonymous_greeting_is_shared_and_local(monkeypatch,market,name):
    review=S(text='Спасибо',rating=5,marketplace=market,buyer_name=name)
    request=AsyncMock(side_effect=AssertionError('No model needed for a local greeting'))
    monkeypatch.setattr('app.integrations.ollama.structured',request)
    answer,verdict=asyncio.run(policy.enforce_owner_rules('Добрый день! Спасибо за отзыв.',review,S(name='Товар'),RULES,[],lambda text:{'passed':True,'score':100}))
    assert answer=='Здравствуйте. Спасибо за отзыв.'
    assert verdict['passed'] and verdict['checked']
    request.assert_not_called()


@pytest.mark.parametrize('opening',['Татьяна, здравствуйте!','Здравствуйте, Пользователь Ozon!','Здравствуйте, Анна!','Добрый день!'])
def test_hidden_name_is_never_used_as_an_address(opening):
    answer,_=policy.apply_local_preferences(opening+' Спасибо! Покупка понравилась.',S(text='',rating=5),RULES)
    assert answer=='Здравствуйте. Спасибо! Покупка понравилась.'


def test_visible_verified_name_does_not_activate_anonymous_rule():
    review=S(text='',rating=5,buyer_name='Анна')
    assert policy.context_constraints(RULES,review)['greeting']=='Добрый день!'
    assert policy.context_constraints(RULES,S(buyer_name='Анна',buyer_name_hidden=True))['greeting']=='Здравствуйте.'


def test_additional_conditional_requirements_are_not_locally_approved():
    rule=ANONYMOUS.strip(' .')+' и укажи наличие товара.'
    assert policy.constraints([rule])['semantic']


@pytest.mark.parametrize('market',['wb','ozon','yandex'])
def test_empty_positive_rating_stays_on_fast_path(market):
    review=S(text='',rating=5,marketplace=market,risk='NORMAL',safety_critical=False)
    answer=positive_template(review,RULES,0,product=S(name='Товар',brand='',category=''))
    assert answer and answer.startswith('Здравствуйте. ')
