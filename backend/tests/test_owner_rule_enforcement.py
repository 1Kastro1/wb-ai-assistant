"""Owner rules must affect the saved answer, including templates and outages."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, func

from app import services
from app.db import Session, Product, Review, Memory, Draft, Action
from app.owner_reply_rules import apply_owner_preferences, matching_owner_rules
from app.owner_reply_policy import enforce_owner_rules, constraints, fingerprint


HELLO = 'Каждый ответ начинай с «Добрый день!».'
COMPOUND = 'Не пиши «Здравствуйте», каждый ответ начинай с «Добрый день!».'
SEMANTIC = 'В ответе поблагодари покупателя за подробное описание доставки.'
PASS = {'compliant': True, 'violations': [], 'revised_answer': ''}


@pytest.fixture
def models(monkeypatch):
    variants = AsyncMock(side_effect=RuntimeError('model unavailable'))
    chat = AsyncMock(side_effect=RuntimeError('model unavailable'))
    check = AsyncMock(return_value=dict(PASS))
    monkeypatch.setattr(services.ollama, 'reply_variants', variants)
    monkeypatch.setattr(services.ollama, 'chat', chat)
    monkeypatch.setattr(services.ollama, 'structured', check)
    monkeypatch.setattr(services.web_research, 'search_instructions', AsyncMock(return_value=[]))
    return variants, chat, check


def seed(db, marketplace='wb', text='Хороший товар', rating=5):
    product = Product(id='owner-rule-product', name='Товар для дома', brand='TEST')
    db.add(product)
    db.flush()
    review = Review(id='owner-rule-row', wb_review_id='owner-rule-remote',
                    wb_account_id='owner', product_id=product.id, text=text,
                    rating=rating, marketplace=marketplace)
    db.add(review)
    db.commit()
    return review, product


@pytest.mark.parametrize('original', [
    'Ответ по существу.', 'Здравствуйте! Ответ по существу.',
    'Добрый вечер! Ответ по существу.', 'Доброе утро, ответ по существу.',
])
def test_explicit_greeting_is_inserted_or_replaced(original):
    reply, _ = apply_owner_preferences(original, SimpleNamespace(text='Вопрос'),
                                       ['Каждый ответ начинай с «Доброго дня!».'])
    assert reply.startswith('Доброго дня!')
    assert 'по существу' in reply
    assert reply.count('Доброго дня!') == 1


def test_compound_negative_clause_does_not_delete_required_greeting():
    result, _ = apply_owner_preferences('Здравствуйте! Спасибо за отзыв.',
                                        SimpleNamespace(text='Хорошо'), [COMPOUND])
    assert result == 'Добрый день! Спасибо за отзыв.'


def test_banned_word_is_not_removed_from_other_words():
    result, _ = apply_owner_preferences('Товарный вид хороший. Товар целый.',
        SimpleNamespace(text='Хорошо'), ['Не используй слово «товар».'])
    assert result.startswith('Товарный вид')
    assert 'Товар целый' not in result


@pytest.mark.parametrize('marketplace', ['wb', 'ozon', 'yandex', 'wb_question', 'ozon_question', 'yandex_question'])
def test_generation_regeneration_and_outage_obey_the_correct_audience(client, models, marketplace):
    questions = marketplace.endswith('_question')
    kind = 'QUESTION_RULE' if questions else 'REVIEW_RULE'
    opposite = 'REVIEW_RULE' if questions else 'QUESTION_RULE'
    with Session() as db:
        row, _ = seed(db, marketplace, 'Из чего сделан товар?' if questions else 'Хороший товар', 0 if questions else 5)
        db.add_all([Memory(id='hello', text=COMPOUND, kind=kind),
                    Memory(id='other-audience', text='Каждый ответ начинай с «Чужое правило!» .', kind=opposite)])
        db.commit()
        for _ in range(2):
            draft = asyncio.run(services.generate_draft(db, row.id))
            assert draft['text'].startswith('Добрый день!')
            assert 'Чужое правило' not in draft['text']
            assert draft['quality']['owner_rules']['matched'] == 1
            assert draft['quality']['owner_rules']['passed'] is True
        models[2].assert_not_awaited()


def test_question_signature_is_applied_even_without_a_model(client, models):
    with Session() as db:
        row, _ = seed(db, 'wb_question', 'Из чего корпус?', 0)
        db.add(Memory(id='signature', kind='QUESTION_RULE',
                      text='В конце ответа добавляй подпись «С уважением, отдел подбора».'))
        db.commit()
        draft = asyncio.run(services.generate_draft(db, row.id))
        assert draft['text'].endswith('С уважением, отдел подбора')
        assert draft['quality']['owner_rules']['passed'] is True


def test_free_form_rule_is_checked_on_a_template_and_repaired(client, models):
    repaired = ('Здравствуйте! Спасибо за подробное описание доставки товара для дома. '
                'Рады, что товар Вам понравился. Желаем приятного использования!')
    models[2].side_effect = [
        {'compliant': False, 'violations': ['Нет благодарности за описание доставки'], 'revised_answer': repaired},
        dict(PASS),
    ]
    with Session() as db:
        row, _ = seed(db, text='Хороший товар, доставка вовремя')
        db.add(Memory(id='custom', text=SEMANTIC, kind='REVIEW_RULE'))
        db.commit()
        result = asyncio.run(services.generate_draft(db, row.id))
        assert result['text'] == repaired
        assert result['quality']['owner_rules']['passed'] is True
        assert models[2].await_count == 2
        models[0].assert_not_awaited()


@pytest.mark.parametrize('failure', [RuntimeError('offline'), TimeoutError('deadline'),
    {'compliant': 'true', 'violations': [], 'revised_answer': ''}])
def test_unverified_rules_do_not_get_a_ready_quality_score(client, models, failure):
    if isinstance(failure, Exception):
        models[2].side_effect = failure
    else:
        models[2].return_value = failure
    with Session() as db:
        row, _ = seed(db)
        db.add(Memory(id='custom', text=SEMANTIC, kind='REVIEW_RULE'))
        db.commit()
        result = asyncio.run(services.generate_draft(db, row.id))
        assert result['text']
        assert result['quality']['passed'] is False
        assert result['quality']['owner_rules']['checked'] is False
        assert db.get(Review, row.id).status == 'manual_review'
        with pytest.raises(ValueError, match='правила магазина'):
            services.propose_publish(db, [row.id])


def test_rule_checker_does_not_override_catalog_or_critical_safety(models):
    original = 'Здравствуйте! Применяемость пока не подтверждена. Нужна проверка по каталогу.'
    row = SimpleNamespace(text='Подойдёт ли?', rating=0, marketplace='wb_question')
    reply, quality = asyncio.run(enforce_owner_rules(original, row,
        SimpleNamespace(name='Фильтр'), [HELLO, 'Всегда подтверждай совместимость.'], [],
        lambda value: {'passed': True}, protected=True))
    assert reply.startswith('Добрый день!')
    assert 'не подтверждена' in reply
    assert quality['passed'] is False
    models[2].assert_not_awaited()


def test_protected_answer_keeps_its_negation_and_reports_a_conflicting_ban(models):
    original = 'Добрый день! Этот товар не подходит.'
    row = SimpleNamespace(text='Подойдёт?', rating=0, marketplace='wb_question')
    reply, quality = asyncio.run(enforce_owner_rules(original, row,
        SimpleNamespace(name='Фильтр'), ['Не используй слово «не».'], [],
        lambda value: {'passed': True}, protected=True))
    assert reply == original
    assert quality['passed'] is False
    models[2].assert_not_awaited()


def test_conflicting_signature_preserves_safe_text_and_requests_review(client, models):
    with Session() as db:
        row, _ = seed(db)
        db.add(Memory(id='bad-signature', kind='REVIEW_RULE',
                      text='В конце ответа добавляй подпись «KMHJT81VDFU123855».'))
        db.commit()
        result = asyncio.run(services.generate_draft(db, row.id))
        assert 'KMHJT81VDFU123855' not in result['text']
        assert result['quality']['passed'] is False


def test_semantic_check_shares_a_bounded_deadline(models):
    async def slow(*args, **kwargs):
        await asyncio.sleep(60)
    models[2].side_effect = slow
    row = SimpleNamespace(text='Отзыв', rating=5, marketplace='wb')
    reply, quality = asyncio.run(enforce_owner_rules('Здравствуйте! Спасибо за отзыв.', row,
        SimpleNamespace(name='Товар'), [SEMANTIC], [], lambda value: {'passed': True},
        check_seconds=0.01))
    assert reply == 'Здравствуйте! Спасибо за отзыв.'
    assert not quality['checked'] and not quality['passed']


@pytest.mark.parametrize('marketplace', ['wb', 'ozon', 'yandex'])
def test_batch_checker_timeout_keeps_draft_and_continues_without_retry(client, models, marketplace):
    from app import main
    from app.db import Job, GenerationOutcome
    models[2].side_effect = TimeoutError('checker deadline')
    with Session() as db:
        row, product = seed(db, marketplace=marketplace, text='Хороший товар, доставка вовремя')
        second = Review(id='next-review', wb_review_id='next-remote', wb_account_id='owner',
                        product_id=product.id, text='Хороший товар, доставка вовремя', rating=5,
                        marketplace=marketplace)
        db.add_all([second, Memory(id='custom', text=SEMANTIC, kind='REVIEW_RULE'),
                    Job(id='checker-outage-batch', kind='drafts', result={'account_id': 'owner'})])
        db.commit()
    asyncio.run(main.run_sync_job('checker-outage-batch'))
    with Session() as db:
        job = db.get(Job, 'checker-outage-batch')
        assert job.status == 'completed'
        assert job.result['processed'] == job.result['attention'] == 2
        assert job.result['failed'] == job.result['retrying'] == 0
        assert db.scalar(select(func.count()).select_from(GenerationOutcome)) == 2
        for rid in (row.id, 'next-review'):
            draft = db.scalar(select(Draft).where(Draft.review_id == rid))
            assert draft.text
            assert not draft.quality['passed']
            assert not draft.quality['owner_rules']['checked']
            assert db.get(Review, rid).status == 'manual_review'
            with pytest.raises(ValueError, match='правила магазина'):
                services.propose_publish(db, [rid])
    assert models[2].await_count == 2


def test_default_checker_deadline_is_bounded_even_during_model_repair(models, monkeypatch):
    from app.generation_budget import GenerationBudget
    budgets = []
    real_init = GenerationBudget.__init__
    def record_budget(self, seconds):
        budgets.append(seconds)
        real_init(self, seconds)
    monkeypatch.setattr(GenerationBudget, '__init__', record_budget)
    models[2].side_effect = [
        {'compliant': False, 'violations': ['Нужна благодарность'],
         'revised_answer': 'Здравствуйте! Спасибо за подробное описание доставки.'},
        dict(PASS),
    ]
    _, quality = asyncio.run(enforce_owner_rules('Здравствуйте! Спасибо за отзыв.',
        SimpleNamespace(text='Доставка вовремя', rating=5, marketplace='wb'),
        SimpleNamespace(name='Товар'), [SEMANTIC], [], lambda value: {'passed': True}))
    assert quality['passed']
    assert budgets == [45]


@pytest.mark.parametrize('candidate', [
    'Здравствуйте! Вернём деньги за товар.',
    'Здравствуйте! Используйте 999 мл средства для дома. Спасибо за отзыв.',
])
def test_rule_repair_cannot_add_unsafe_promises_or_unsupported_numbers(models, candidate):
    models[2].return_value = {'compliant': False, 'violations': ['Нужно уточнить ответ'], 'revised_answer': candidate}
    original = 'Здравствуйте! Спасибо за отзыв о товаре для дома. Желаем приятного использования.'
    row = SimpleNamespace(text='Отзыв', rating=5, marketplace='wb')
    result, quality = asyncio.run(enforce_owner_rules(original, row,
        SimpleNamespace(name='Товар для дома'), [SEMANTIC], [], lambda value: {'passed': True}))
    assert result == original
    assert quality['passed'] is False


@pytest.mark.parametrize('kind', ['REVIEW_RULE', 'QUESTION_RULE'])
def test_preview_obeys_rules_without_saving_anything(client, models, kind):
    with Session() as db:
        before = {model: db.scalar(select(func.count()).select_from(model)) for model in (Memory, Review, Draft, Action)}
    result = client.post('/memory/preview', json={'kind': kind, 'text': COMPOUND,
        'example': 'Из чего корпус?' if kind == 'QUESTION_RULE' else 'Хороший товар', 'rating': 5})
    assert result.status_code == 200, result.text
    assert result.json()['text'].startswith('Добрый день!')
    assert result.json()['quality']['owner_rules']['passed'] is True
    with Session() as db:
        assert all(db.scalar(select(func.count()).select_from(model)) == count for model, count in before.items())


def test_new_rule_invalidates_old_draft_without_overwriting_it(client, models):
    with Session() as db:
        row, product = seed(db)
        generated = asyncio.run(services.generate_draft(db, row.id))
        db.add(Memory(id='new-rule', kind='REVIEW_RULE', text=HELLO))
        db.commit()
        draft = db.get(Draft, generated['id'])
        shown = services.draft_public(db, draft)
        assert shown['text'] == generated['text']
        assert shown['quality']['passed'] is False
        with pytest.raises(ValueError, match='правила магазина'):
            services.propose_publish(db, [row.id])
        refreshed = asyncio.run(services.generate_draft(db, row.id))
        assert refreshed['text'].startswith('Добрый день!')
        assert refreshed['quality']['owner_rules']['passed']
        assert services.propose_publish(db, [row.id])['kind'] == 'publish'


def test_rule_change_between_proposal_and_confirmation_requires_recheck(client, models):
    with Session() as db:
        row, _ = seed(db)
        asyncio.run(services.generate_draft(db, row.id))
        action = services.propose_publish(db, [row.id])
        db.add(Memory(id='new-rule', kind='REVIEW_RULE', text=HELLO))
        db.commit()
        with pytest.raises(ValueError, match='Правила магазина'):
            asyncio.run(services.confirm_action(db, action['id']))
        db.rollback()
        assert not db.get(Review, row.id).is_answered


def test_scope_whitespace_and_specific_product_rule_work(client):
    with Session() as db:
        _, product = seed(db)
        db.add_all([Memory(id='a', text=HELLO, kind='REVIEW_RULE'),
                    Memory(id='b', text='brand', scope=' brand : TEST ', kind='REVIEW_RULE'),
                    Memory(id='c', text='specific', scope='product:owner-rule-product', kind='REVIEW_RULE'),
                    Memory(id='d', text='disabled', enabled=False)])
        db.commit()
        assert matching_owner_rules(db, product) == [HELLO, 'brand', 'specific']


def test_signature_and_other_requirements_in_one_rule_are_not_silently_skipped():
    result = constraints('В конце добавляй подпись «С уважением». И поблагодари за доставку.'.split('\n'))
    assert result['signature'] == 'С уважением'
    assert result['semantic']


def test_conditional_greeting_does_not_become_an_unconditional_substitution():
    rules = ['Если отзыв негативный, начинай с «Приносим извинения!».']
    result, _ = apply_owner_preferences('Здравствуйте! Спасибо за положительный отзыв.',
                                       SimpleNamespace(text='Всё понравилось'), rules)
    assert result.startswith('Здравствуйте!')
    assert constraints(rules)['semantic']


def test_custom_owner_greeting_is_not_rejected_by_question_quality(client, models):
    with Session() as db:
        row, _ = seed(db, 'wb_question', 'Из чего корпус товара?', 0)
        db.add(Memory(id='custom-hello', kind='QUESTION_RULE', text='Начинай с «Доброго дня!» .'))
        db.commit()
        draft = asyncio.run(services.generate_draft(db, row.id))
        assert draft['text'].startswith('Доброго дня!')
        assert 'добавить дружелюбное приветствие' not in draft['quality']['issues']
        assert draft['quality']['owner_rules']['passed']


def test_current_rules_for_a_large_page_are_loaded_once(client):
    from sqlalchemy import event
    from app.db import engine
    with Session() as db:
        for i in range(30):
            product = Product(id=f'page-product-{i}', name='Товар')
            db.add(product)
            db.flush()
            row = Review(id=f'page-review-{i}', wb_review_id=str(i), product_id=product.id,
                         wb_account_id='owner', text='Хорошо', rating=5)
            db.add(row)
            db.flush()
            db.add(Draft(id=f'page-draft-{i}', review_id=row.id, text='Здравствуйте! Спасибо.', original='old'))
        db.add(Memory(id='page-rule', kind='REVIEW_RULE', text=HELLO))
        db.commit()
    queries = []
    def count(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('SELECT'):
            queries.append(statement)
    event.listen(engine, 'before_cursor_execute', count)
    try:
        with Session() as db:
            page = services.reviews_page(db, account_id='owner', limit=30)
            assert len(page['items']) == 30
            assert all(not item['draft']['quality']['owner_rules']['passed'] for item in page['items'])
            assert len(queries) <= 6
    finally:
        event.remove(engine, 'before_cursor_execute', count)
