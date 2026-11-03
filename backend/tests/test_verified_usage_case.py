import asyncio
import pytest
from unittest.mock import AsyncMock
from sqlalchemy import select
from app import services
from app.db import Session, Product, Review, Draft, Setting
from app.rule_execution import audited_usage_replies
from app.owner_reply_rules import matching_owner_rules
from app.learned_reply_templates import load_template
from test_new_variant_context import seed

FACT = {'kind':'instruction','source':'Карточка товара','verification_status':'VERIFIED',
        'text':'Нанесите средство на поверхность с помощью губки или мягкой салфетки. Оставьте на 5 минут. Протрите, удаляя грязь и остатки средства.'}


@pytest.mark.parametrize('provider', ['wb','ozon','yandex'])
def test_current_evidence_recipe_is_freshly_checked_and_learned_without_model(client, monkeypatch, provider):
    from app.integrations import ollama
    forbidden = AsyncMock(side_effect=AssertionError('Exact audited recipe uses current evidence'))
    monkeypatch.setattr(ollama,'structured',forbidden)
    monkeypatch.setattr(ollama,'reply_variants',forbidden)
    with Session() as db:
        seed(db,provider)
        db.get(Product,'lotion').facts=[FACT]; db.commit()
        first=asyncio.run(services.generate_draft(db,'buyer'))
        assert first['quality']['passed'], first['quality']
        assert first['quality']['owner_rules']['verification_method']=='local_verified_usage'
        assert '5 минут' in first['text'] and 'скользкой' in first['text']
        second=asyncio.run(services.generate_draft(db,'buyer'))
        assert second['text']!=first['text'] and second['quality']['passed']
        assert list(db.scalars(select(Setting).where(Setting.key.startswith('verified-replies:'))))
        p,r=db.get(Product,'lotion'),db.get(Review,'buyer')
        rules=matching_owner_rules(db,p)
        facts=services.review_verified_facts(db,r,p,rules)
        assert load_template(db,r,p,rules,facts,lambda value:services.assess_reply(r,p,value,facts),previous=second['text'])
        assert not audited_usage_replies(r,p,[*rules,'Новое произвольное правило'],facts)
        assert not audited_usage_replies(r,p,rules,[])
        assert not audited_usage_replies(r,p,rules,[{**FACT,'verification_status':'UNVERIFIED'}])
        assert not audited_usage_replies(r,p,rules,[{**FACT,'text':'Состав неизвестен'}])
        assert first['text']+' Дополнительное обещание.' not in audited_usage_replies(r,p,rules,facts)
        for change in ('Кожа стала скользкой, не чистит. Появилось раздражение.',
                       'Не чистит кожу, она стала скользкой. Наносил по инструкции.',
                       'Не чистит кожу, она стала скользкой. Верните деньги.',
                       'Не чистит кожу и скользит, товар протёк.',
                       'Не чистит кожу, она стала скользкой. Как быть?'):
            r.text=change
            assert not audited_usage_replies(r,p,rules,facts)
    forbidden.assert_not_awaited()


def test_unknown_policy_still_requires_semantic_check_and_timeout_is_not_approval(client,monkeypatch):
    from app.integrations import ollama
    from app.db import Memory
    checker=AsyncMock(side_effect=TimeoutError())
    monkeypatch.setattr(ollama,'structured',checker)
    with Session() as db:
        seed(db,'wb')
        db.get(Product,'lotion').facts=[FACT]
        db.add(Memory(id='extra',scope='global',kind='REVIEW_RULE',text='Объясняй ответ строго по новому требованию владельца.'))
        db.commit()
        result=asyncio.run(services.generate_draft(db,'buyer'))
        assert not result['quality']['passed']
        assert result['quality']['owner_rules']['technical_error']
        assert not list(db.scalars(select(Setting).where(Setting.key.startswith('verified-replies:'))))
    assert checker.await_count
