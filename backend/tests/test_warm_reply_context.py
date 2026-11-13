import asyncio
import re
from unittest.mock import AsyncMock
import pytest
from app import services
from app.db import Session, Product, Review, Memory, Job
from sqlalchemy import select
from app.reply_policy import generation_policy, has_reply_body
from app.model_context import draft_messages, request_tokens


@pytest.mark.parametrize('text,rating,product', [
    ('Светят норм. Через месяц перестали работать.', 3, 'LED лампы Avantech MAX HB4'),
    ('хороший фильтр по хорошей цене, ставлю на sx4', 5, 'Фильтр Nitto 4SF-103'),
    ('маленькая лопатка, для поездок на природу, больших возможностей не ждите', 5, 'Лопата iSky iSUS-19'),
])
def test_screenshot_cases_fit_preparation_context(text, rating, product):
    rules = ['Если есть жалоба: ' + 'учитывай условия. ' * 1000] * 8
    messages = draft_messages(generation_policy(), {'buyer_message': text, 'rating': rating, 'product': product},
                              rules, [{'text': 'Факт. ' * 5000}])
    assert request_tokens(messages, predict=768) < 6000
    assert text in messages[-1]['content']


@pytest.mark.parametrize('answer', ['Здравствуйте.\nС уважением ГК Юником.', 'Добрый день! С уважением ГК Юником.'])
def test_greeting_and_signature_is_not_content(answer):
    assert not has_reply_body(answer)


def test_destructive_verifier_never_saves_a_bare_greeting(client, monkeypatch):
    async def skeleton(*args, **kwargs):
        return 'Здравствуйте.\nС уважением ГК Юником.', {'passed':False,'checked':False,'technical_error':True,'issues':['timeout']}
    monkeypatch.setattr(services,'enforce_owner_rules',skeleton)
    monkeypatch.setattr(services.ollama,'reply_variants',AsyncMock(side_effect=TimeoutError()))
    with Session() as db:
        product=Product(id='warm-lamp',name='Лампы H1 Avantech AB5001',brand='Avantech')
        review=Review(id='warm-review',wb_review_id='warm-remote',wb_account_id='owner',product_id=product.id,marketplace='yandex',
            rating=4,text='работают, упаковано было хорошо. поменял в дальнем, разницу не заметил, может кто нагрузку мерял? может там и нет 55?',risk='NORMAL')
        db.add_all([product,review,Memory(id='warm-sign',kind='REVIEW_RULE',scope='global',
            text='В конце каждого ответа на отзыв добавляй подпись «С уважением ГК Юником.».'),
            Memory(id='warm-semantic',kind='REVIEW_RULE',scope='global',
                   text='При сомнениях в мощности объясняй пределы проверки без дополнительных обещаний.')])
        db.commit()
        draft=asyncio.run(services.generate_draft(db,review.id))
        assert has_reply_body(draft['text'])
        assert 'спасибо' in draft['text'].lower() and 'мощност' in draft['text'].lower()
        assert not re.search(r'55\s*(?:Вт|ватт)',draft['text'])
        assert not draft['quality']['owner_rules']['checked'] and not draft['quality']['passed']
        assert draft['text'].endswith('С уважением ГК Юником.')
        assert db.scalar(select(Job).where(Job.kind=='draft_recheck')) is not None
