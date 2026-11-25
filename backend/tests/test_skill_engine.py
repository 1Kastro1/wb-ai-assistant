import asyncio
from unittest.mock import AsyncMock

from sqlalchemy import select

from app import services
from app.db import Draft, Product, Publication, Review, Session


def test_owner_can_enable_traces_and_roll_back_without_changing_reply_flow(client, monkeypatch):
    def run_reply():
        with Session() as db:
            return asyncio.run(services.generate_draft(db, 'review'))

    with Session() as db:
        db.add(Product(id='product', name='Ароматизатор', brand='EIKOSHA'))
        db.flush()
        db.add(Review(id='review', wb_review_id='review', product_id='product',
                      text='Все супер!', rating=5, marketplace='wb'))
        db.commit()

    model = AsyncMock(side_effect=AssertionError('Known praise needs no model'))
    monkeypatch.setattr(services.ollama, 'reply_variants', model)
    report = client.get('/ai-skills').json()
    assert report['enabled'] is False
    assert all(not entry['active'] for entry in report['skills'])
    assert client.patch('/ai-skills', json={'enabled': True}).json()['enabled'] is True
    draft = run_reply()
    trace = draft['quality']['skill_engine']
    assert trace['kind'] == 'review' and trace['passed']
    assert {step['id'] for step in trace['steps']} == {
        'review_classifier', 'product_knowledge', 'quality_control', 'policy_checker',
    }
    with Session() as db:
        assert db.get(Draft, draft['id']).quality['skill_engine'] == trace
        assert not db.scalar(select(Publication))
        assert not db.get(Review, 'review').is_answered
    model.assert_not_awaited()
    assert client.patch('/ai-skills', json={'enabled': False}).json()['enabled'] is False
    assert 'skill_engine' not in run_reply()['quality']
    client.post('/auth/logout')
    assert client.get('/ai-skills').status_code == 401
    assert client.patch('/ai-skills', json={'enabled': True}).status_code == 401


def test_question_records_model_failure_and_checked_fallback(client, monkeypatch):
    assert client.patch('/ai-skills', json={'enabled': True}).status_code == 200
    monkeypatch.setattr(services.ollama, 'reply_variants', AsyncMock(side_effect=TimeoutError))
    with Session() as db:
        db.add(Product(id='product', name='Ароматизатор', brand='EIKOSHA'))
        db.flush()
        question = Review(id='question', wb_review_id='question', product_id='product',
                          text='Какой объём флакона?', rating=0, marketplace='wb_question')
        db.add(question)
        db.commit()
        result = asyncio.run(services.generate_question_draft(db, question))
        trace = result['quality']['skill_engine']
        assert trace['kind'] == 'question' and trace['route'] == 'fallback'
        steps = {step['id']: step for step in trace['steps']}
        assert steps['model_generation']['status'] == 'error'
        assert steps['policy_checker']['status'] == 'completed'
        assert trace['passed'] is result['quality']['passed']
        assert not question.is_answered
