import asyncio
from unittest.mock import AsyncMock
from app.integrations import OllamaProvider, guard, ollama
from app.db import Product, Review, Session
from app import services


def test_single_question_variant_uses_bounded_structured_output(client, monkeypatch):
    async def request(service, operation, **kwargs):
        assert (service, operation) == ('ollama', 'chat')
        body = kwargs['body']
        assert body['options']['num_predict'] <= 256
        assert body['format']['properties']['variants']['maxItems'] == 1
        assert body['think'] is False
        return {'message': {'content': '{"variants":["Здравствуйте! Корпус изготовлен из полипропилена."]}'}}
    monkeypatch.setattr(guard, 'request', request)
    assert asyncio.run(OllamaProvider().reply_variants([{'role':'user','content':'Из чего корпус?'}], 1)) == ['Здравствуйте! Корпус изготовлен из полипропилена.']


def test_question_cold_start_has_a_finite_budget_without_generating_three_variants(client, monkeypatch):
    with Session() as db:
        db.add(Product(id='material-product', name='Контейнер', brand='OTHER', facts=[
            {'text':'Материал корпуса: полипропилен', 'source':'Карточка WB: характеристики','verification_status':'VERIFIED'}]))
        db.flush()
        db.add(Review(id='material-question', wb_review_id='question:material', product_id='material-product',
                      wb_account_id='owner', marketplace='wb_question', rating=0, text='Из какого материала корпус?'))
        db.commit()
    calls = []
    async def variants(messages, count):
        calls.append(count)
        return ['Здравствуйте! Корпус изготовлен из полипропилена.']
    real_budget = services.GenerationBudget
    def budget(seconds=30):
        # Simulate a model that needs more than the previous 30-second budget
        # for prompt evaluation, without sleeping in the regression suite.
        assert 30 < seconds <= 60
        value = real_budget(seconds)
        value.deadline -= 35
        return value
    monkeypatch.setattr(ollama, 'reply_variants', variants)
    monkeypatch.setattr(services, 'GenerationBudget', budget)
    with Session() as db:
        result = asyncio.run(services.generate_question_draft(db, db.get(Review, 'material-question')))
        assert 'полипропилен' in result['text']
        assert result['quality']['timing']['route'] == 'model'
        assert result['quality']['passed'] is True
    assert calls == [1]
