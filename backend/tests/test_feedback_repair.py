import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import production_date
from app.db import Draft, Job, Memory, Product, Review, Session
from app.draft_audit import recheck_step
from app.rule_retry import enqueue


@pytest.mark.parametrize('is_question', [False, True])
def test_saved_findings_create_new_revision_and_complete_fresh_rule_check(client, monkeypatch, is_question):
    from app import services
    from app.integrations import ollama
    from app.owner_reply_rules import matching_owner_rules, owner_business_facts
    from app.rule_execution import verification_key

    original = 'Здравствуйте! Спасибо за обратную связь. Хорошего дня!'
    text = '55б24r год и месяц производства?' if is_question else 'Пока вроде пахнет, посмотрим на сколько хватит!'
    checker = AsyncMock(return_value={'compliant': True, 'violations': [], 'revised_answer': ''})
    monkeypatch.setattr(ollama, 'structured', checker)
    monkeypatch.setattr(ollama, 'reply_variants', AsyncMock(side_effect=AssertionError('grounded deterministic repair')))
    with Session() as db:
        product = Product(id='p', name='Аккумулятор 55B24R' if is_question else 'Ароматизатор', facts=[
            {'text': 'Дата регистрации сертификата: 25.06.2025', 'source': 'Карточка товара', 'verification_status': 'VERIFIED'}])
        db.add(product)
        db.flush()
        review = Review(id='r', wb_account_id='owner', wb_review_id='r', product_id='p', text=text,
                        marketplace='wb_question' if is_question else 'wb', rating=0 if is_question else 5)
        db.add(review)
        db.add(Memory(id='rule', kind='QUESTION_RULE' if is_question else 'REVIEW_RULE',
                      text='Отвечай по существу с учётом обстоятельств обращения.', enabled=True))
        db.flush()
        rules = matching_owner_rules(db, product, for_questions=is_question)
        facts = (services.trusted_product_facts(db, product) + owner_business_facts(product, rules)
                 if is_question else services.review_verified_facts(db, review, product, rules))
        key = verification_key(original, review, product, rules, facts)
        draft = Draft(id='d', review_id='r', text=original, original=original, quality={
            'passed': False, 'owner_rules': {'checked': True, 'passed': not is_question, 'input_key': key,
                                           'violations': ['Уточнить месяц и год выпуска'] if is_question else []}})
        db.add(draft)
        db.flush()
        job_id = enqueue(db, draft, review, confirmed_repair=True).id
        db.commit()

    asyncio.run(recheck_step(job_id))
    with Session() as db:
        draft = db.get(Draft, 'd')
        assert draft.revision == 2 and draft.original == original
        assert ('точный месяц и год' if is_question else 'аромат уже чувствуется') in draft.text
        assert '2025' not in draft.text and db.get(Job, job_id).status == 'queued'
        assert draft.quality['confirmed_repair']['status'] == 'pending'
        assert draft.quality['passed'] is False
    checker.assert_not_awaited()
    asyncio.run(recheck_step(job_id))
    with Session() as db:
        draft = db.get(Draft, 'd')
        assert draft.quality['confirmed_repair']['status'] == 'rechecked'
        assert draft.quality['owner_rules']['checked'] and draft.quality['passed']
    checker.assert_awaited_once()


def test_only_verified_dates_for_exact_inquiry_survive_conflict_and_certificate_guard():
    question = SimpleNamespace(id='current', marketplace='wb_question', text='55б24r год и месяц производства?')
    record = {'kind': 'production_date', 'review_id': question.id, 'date': '2025-08-17', 'verification_status': 'VERIFIED'}
    assert production_date.claim_issue(question, 'Дата выпуска — 25.06.2025.', [{'date': '2025-06-25'}])
    assert production_date.claim_issue(question, 'Выпущен в мае.', [])
    assert production_date.confirmed_month(question, [{**record, 'review_id': 'another'}]) == ''
    assert production_date.confirmed_month(question, [{**record, 'verification_status': 'WEB_UNVERIFIED'}]) == ''
    assert production_date.confirmed_month(question, [record, {**record, 'date': '2025-09'}]) == ''
    assert not production_date.claim_issue(question, 'Месяц и год выпуска — 08.2025.', [record])
    assert production_date.claim_issue(question, 'Месяц и год выпуска — 08.2025.', [])


def test_recheck_fixes_only_required_greeting_before_semantic_verification(client, monkeypatch):
    from app.integrations import ollama

    original = 'Здравствуйте. Спасибо за отзыв. Сожалеем, что результат Вас разочаровал.'
    checker = AsyncMock(return_value={'compliant': True, 'violations': [], 'revised_answer': ''})
    monkeypatch.setattr(ollama, 'structured', checker)
    monkeypatch.setattr(ollama, 'reply_variants', AsyncMock(side_effect=AssertionError('format-only repair')))
    with Session() as db:
        db.add(Product(id='p', name='Автотовар'))
        db.flush()
        review = Review(id='r', wb_account_id='owner', wb_review_id='r', product_id='p',
                        text='Не оправдал ожиданий', marketplace='yandex', rating=1)
        db.add(review)
        db.add(Memory(id='opening', text='Начинай ответ с «Здравствуйте!».',
                      kind='REVIEW_RULE', enabled=True))
        db.flush()
        draft = Draft(id='d', review_id='r', text=original, original=original, quality={})
        db.add(draft)
        db.flush()
        job_id = enqueue(db, draft, review, confirmed_repair=True).id
        db.commit()

    asyncio.run(recheck_step(job_id))
    with Session() as db:
        draft = db.get(Draft, 'd')
        assert draft.text == original.replace('Здравствуйте.', 'Здравствуйте!', 1)
        assert draft.quality['confirmed_repair']['status'] == 'pending'
        assert not draft.quality['passed']
    checker.assert_not_awaited()

    for _ in range(4):
        asyncio.run(recheck_step(job_id))
        with Session() as db:
            if db.get(Job, job_id).status == 'completed':
                break
    with Session() as db:
        draft = db.get(Draft, 'd')
        assert draft.quality['passed']
        assert draft.quality['confirmed_repair']['status'] == 'rechecked'
