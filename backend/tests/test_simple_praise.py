import asyncio
from types import SimpleNamespace as S
from unittest.mock import AsyncMock
import pytest
from app import rule_execution as execution
from app.db import Session, Product, Review, Memory, Draft, Action
from app.services import generate_draft
from app.owner_reply_policy import enforce_owner_rules


def row(text, **extra):
    return S(id='praise',text=text,rating=5,risk='NORMAL',manual=False,safety_critical=False,marketplace='wb',**extra)


@pytest.mark.parametrize('text',['Всё супер!','Все отлично!!!','  Хороший товар.  ','Рекомендую','Спасибо!'])
def test_only_exact_complete_praise_is_eligible(text):
    assert execution.simple_positive(row(text))


@pytest.mark.parametrize('text',[
    'Все супер, но флакон протек','Не все отлично','Спасибо, но не подошло','Отлично?',
    'Супер? Не работает!','Отлично. Запах слабый','Всё хорошо, электролит не протек',
    'хороший товар\nупаковка плохая','Все супер VIN RD1-5300053','Отлично, можно детям?',
])
def test_extra_meaning_never_enters_the_shortcut(text):
    assert not execution.simple_positive(row(text))


@pytest.mark.parametrize('field,value',[('rating',3),('risk','HIGH'),('manual',True),('safety_critical',True),('marketplace','ozon_question')])
def test_short_praise_preserves_safety_and_personal_approval(field,value):
    review=row('Все супер!');setattr(review,field,value)
    assert not execution.simple_positive(review)


@pytest.mark.parametrize('market',['wb','ozon','yandex'])
def test_reviewed_praise_uses_no_model_and_unknown_rule_opts_out(client,monkeypatch,market):
    from app.integrations import ollama
    judge=AsyncMock(side_effect=AssertionError('Exact praise should not use model'))
    for method in ('structured','chat','reply_variants'):monkeypatch.setattr(ollama,method,judge)
    text='Пиши кратко и доброжелательно, не выдумывай свойства товара.'
    monkeypatch.setattr(execution,'PROFILES',{**execution.PROFILES,execution.digest(text):{'local_positive':True}})
    with Session() as db:
        db.add(Product(id='p',name='Ароматизатор',brand='EIKOSHA'));db.flush()
        db.add(Review(id='r',wb_review_id='r',product_id='p',text='Все супер!',rating=5,marketplace=market))
        db.add(Memory(id='m',kind='REVIEW_RULE',text=text));db.commit()
        result=asyncio.run(generate_draft(db,'r'))
        assert result['quality']['passed'] and result['quality']['owner_rules']['verification_method']=='local_positive', str(result['quality'])
        assert not db.get(Review,'r').is_answered and not list(db.query(Action))
        assert execution.positive_template(db.get(Review,'r'),[text,'Всегда называй номер партии.'],product=db.get(Product,'p')) is None
    judge.assert_not_awaited()


def test_preview_generation_keeps_unknown_rules_but_omits_reviewed_other_categories(client,seeded,monkeypatch):
    from app.integrations import ollama
    conditional='Применяй только к тормозам: проверь ось.'
    custom='Отвечай с учётом пожелания покупателя.'
    monkeypatch.setattr(execution,'PROFILES',{**execution.PROFILES,execution.digest(conditional):{'family':'brakes'}})
    writer=AsyncMock(return_value='Добрый день! Спасибо за отзыв. Рады, что аромат Вам понравился.')
    monkeypatch.setattr(ollama,'chat',writer)
    monkeypatch.setattr(ollama,'structured',AsyncMock(return_value={'compliant':True,'violations':[],'revised_answer':''}))
    with Session() as db:
        db.add(Memory(id='conditional',kind='REVIEW_RULE',text=conditional));db.commit()
    result=client.post('/memory/preview',json={'text':custom,'example':'Приятный аромат','rating':5,'product_id':'p2','kind':'REVIEW_RULE'})
    assert result.status_code==200,result.text
    prompt=writer.call_args.args[0][0]['content']
    assert custom in prompt and conditional not in prompt


def test_preview_deduplicates_selected_rule_and_uses_same_fast_route(client,monkeypatch):
    from app.integrations import ollama
    judge=AsyncMock(side_effect=AssertionError('Preview of exact praise should not use model'))
    monkeypatch.setattr(ollama,'chat',judge);monkeypatch.setattr(ollama,'structured',judge)
    text='Каждый ответ начинай с «Добрый день!». '
    with Session() as db:
        db.add(Memory(id='greeting',kind='REVIEW_RULE',text=text));db.commit()
    response=client.post('/memory/preview',json={'text':text,'example':'Все супер!','rating':5,'kind':'REVIEW_RULE'})
    assert response.status_code==200,response.text
    assert response.json()['quality']['owner_rules']['matched']==1
    assert response.json()['quality']['owner_rules']['verification_method']=='local_positive'
    with Session() as db:
        assert len(list(db.query(Memory)))==1 and not list(db.query(Draft)) and not list(db.query(Action))
    judge.assert_not_awaited()
