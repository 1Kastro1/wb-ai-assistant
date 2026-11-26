import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app import config, main, scheduled_publication, services
from app.db import Account, Draft, Job, Product, Publication, Review, Session, set_setting
from app.simple_question_templates import METHOD, STALE
from app.skill_engine import ENABLED


def count_fact(quantity):
    return {'text': f'Количество в комплекте: {quantity} шт.', 'source': 'Карточка товара',
            'verification_status': 'VERIFIED'}


def prepare(monkeypatch, *, facts):
    monkeypatch.setattr(main, 'run_job_queue', AsyncMock())
    monkeypatch.setattr(config, 'REAL_PUBLISH', True)
    monkeypatch.setattr(services, 'REAL_PUBLISH', True)
    rejected = AsyncMock(side_effect=AssertionError('A verified template must not call Ollama'))
    for method in ('reply_variants', 'chat', 'structured'):
        monkeypatch.setattr(services.ollama, method, rejected)
    with Session() as db:
        set_setting(db, ENABLED, True)
        set_setting(db, 'test_mode', False)
        set_setting(db, 'safe_mode', False)
        db.add(Account(id='owner', encrypted_token='fake-provider'))
        db.add(Product(id='product', name='Лампа', facts=facts))
        db.flush()
        db.add(Review(id='question', wb_review_id='question', wb_account_id='owner', product_id='product',
                      text='Сколько штук в комплекте?', rating=0, marketplace='wb_question'))
        db.add(Review(id='review', wb_review_id='review', wb_account_id='owner', product_id='product',
                      text='Отлично!', rating=5, marketplace='wb'))
        db.commit()
    return rejected


def test_templates_publish_via_real_gate_without_model_or_manual_confirmation(client, monkeypatch):
    rejected = prepare(monkeypatch, facts=[count_fact(2)])
    sends = []

    class Provider:
        def __init__(self, store):
            self.store = store

        async def publish(self, db, review, text):
            sends.append((review.id, text))

    monkeypatch.setattr(services, 'provider_for', Provider)
    with Session() as db:
        question = asyncio.run(services.generate_draft(db, 'question'))
        review = asyncio.run(services.generate_draft(db, 'review'))
        assert question['quality']['owner_rules']['verification_method'] == METHOD
        assert '2 шт.' in question['text']
        for draft in (question, review):
            assert draft['quality']['skill_engine']['route'] == 'template'
            assert draft['quality']['passed'] is True
    response = client.patch('/settings/schedule', json={'enabled': True, 'publication_enabled': True,
        'publication_reviews': True, 'publication_questions': True})
    assert response.status_code == 200, response.text
    with Session() as db:
        job = db.scalar(select(Job).where(Job.kind == scheduled_publication.KIND,
            Job.result['account_id'].as_string() == 'owner'))
        assert job
        job_id = job.id
    asyncio.run(scheduled_publication.run_step(job_id))
    assert dict(sends) == {'question': question['text'], 'review': review['text']}
    with Session() as db:
        assert {publication.status for publication in db.scalars(select(Publication))} == {'published'}
    rejected.assert_not_awaited()


def test_changed_product_count_invalidates_the_saved_template(client, monkeypatch):
    rejected = prepare(monkeypatch, facts=[count_fact(2)])
    with Session() as db:
        recorded = asyncio.run(services.generate_draft(db, 'question'))
        db.get(Product, 'product').facts = [count_fact(3)]
        db.commit()
        with pytest.raises(ValueError, match='шаблона изменились'):
            services.propose_publish(db, ['question'])
        review = db.get(Review, 'question')
        draft = db.get(Draft, recorded['id'])
        assert scheduled_publication.eligibility_reason(db, review, draft, {'publication_questions': True}) == STALE
    rejected.assert_not_awaited()
