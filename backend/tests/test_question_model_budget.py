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
        return {'message': {'content': '{"variants":["Здравствуйте! Корпус этого контейнера изготовлен из полипропилена, как указано в характеристиках товара."]}'}}
    monkeypatch.setattr(guard, 'request', request)
    assert asyncio.run(OllamaProvider().reply_variants([{'role':'user','content':'Из чего корпус?'}], 1)) == ['Здравствуйте! Корпус этого контейнера изготовлен из полипропилена, как указано в характеристиках товара.']


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
        assert len(messages[0]['content']) < 2500
        payload = __import__('json').loads(messages[1]['content'])
        assert payload['verified_facts'][0]['text'] == 'Материал корпуса: полипропилен'
        assert 'verified_at' not in payload['verified_facts'][0]
        assert 'store_rules' in payload and 'owner_instruction' in payload
        return ['Здравствуйте! Корпус этого контейнера изготовлен из полипропилена, как указано в характеристиках товара.']
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


def test_restart_preserves_unstarted_single_answer_but_does_not_replay_running_answer(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from app.db import Draft, Job
    with Session() as db:
        db.add(Product(id='restart-product', name='Контейнер'))
        db.flush()
        db.add(Review(id='restart-question', wb_review_id='question:restart', product_id='restart-product',
                      wb_account_id='owner', marketplace='wb_question', rating=0, text='Из чего корпус?'))
        db.add_all([
            Job(id='waiting-answer', kind='single_question_draft', status='queued', result={'account_id':'owner','selected_ids':['restart-question']}),
            Job(id='running-answer', kind='single_draft', status='running', result={'account_id':'owner','selected_ids':['restart-question']}),
        ])
        db.commit()
    calls = []
    async def generate(db, review_id, instruction=''):
        calls.append(review_id)
        draft = Draft(id='restart-answer', review_id=review_id, text='Подготовленный ответ', original='Подготовленный ответ')
        db.add(draft)
        db.commit()
        return {'id':draft.id}
    monkeypatch.setattr(main, 'generate_draft', generate)
    with TestClient(main.app) as app_client:
        app_client.portal.call(asyncio.sleep, 0)
        with Session() as db:
            assert db.get(Job, 'waiting-answer').status == 'completed'
            assert db.get(Job, 'running-answer').status == 'interrupted'
            assert db.get(Draft, 'restart-answer') is not None
    assert calls == ['restart-question']
