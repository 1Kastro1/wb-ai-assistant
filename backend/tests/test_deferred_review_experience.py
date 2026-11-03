"""Regression cases from real marketplace drafts, shared by all three shops."""
from types import SimpleNamespace
import json
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.product_guidance import positive_detail, positive_review_reply
from app.reply_policy import semantic_reply_issues
from app.owner_reply_policy import constraints, local_issues, apply_local_preferences
from app.rule_execution import positive_template, verified_positive_reply
from app.services import assess_reply


PRODUCT = SimpleNamespace(name='Антижир Kangaroo EcoHome', category='Бытовая химия', facts=[])


@pytest.mark.parametrize('marketplace', ['wildberries', 'ozon', 'yandex'])
@pytest.mark.parametrize('text', [
    'позже дам информацию по качеству',
    'Ещё не пробовал, позже дополню отзыв',
    'Пока не использовал',
    'На вид качественный, ещё не проверял',
])
def test_untried_purchase_does_not_become_positive_experience(marketplace, text):
    review = SimpleNamespace(text=text, rating=5, marketplace=marketplace)
    for revision in range(3):
        reply = positive_review_reply(review, PRODUCT, revision)
        assert reply
        assert not any(phrase in reply.lower() for phrase in (
            'оценили качество', 'покупка вам понравилась', 'поделились опытом', 'и дальше радует',
            'довольны работой', 'оправдал ожидания'))
        assert not semantic_reply_issues(review, PRODUCT, reply)
        assert assess_reply(review, PRODUCT, reply)['passed']


def test_checker_rejects_actual_screenshot_answer():
    review = SimpleNamespace(text='позже дам информацию по качеству', rating=5)
    answer = ('Здравствуйте. Благодарим за обратную связь. Рады, что Вы оценили качество товара. '
              'Пусть покупка и дальше радует Вас! С уважением ГК Юником.')
    assert 'не приписывать опыт использования и довольство до проверки товара' in semantic_reply_issues(review, PRODUCT, answer)


def test_real_praise_and_description_are_preserved_without_invented_expectations():
    assert 'оценили качество' in positive_detail('Отличное качество')
    assert 'результат очистки' in positive_detail('Отлично чистит')
    assert 'ожиданиям' not in positive_detail('Соответствует описанию')
    assert 'пенообразование' not in positive_detail('Не пенится')
    assert 'довольны работой' not in positive_detail('Не работает')


@pytest.mark.parametrize('rule', [
    'Приветствие: «Здравствуйте.» или «Добрый день!», оба допустимы;',
    'Начинай с «Здравствуйте.» или «Добрый день!»: оба варианта допустимы.',
    'Допустимые приветствия: «Здравствуйте.» или «Добрый день!»',
])
def test_both_explicitly_allowed_greetings_pass_and_remain_unchanged(rule):
    for opening in ('Здравствуйте.', 'Добрый день!'):
        answer = opening + ' Спасибо за Ваш отзыв.'
        assert not local_issues(answer, [rule])
        assert apply_local_preferences(answer, SimpleNamespace(text=''), [rule])[0] == answer


def test_greeting_alternatives_do_not_swallow_additional_requirement():
    rule = 'Начинай с «Здравствуйте.» или «Добрый день!», также сообщай номер партии.'
    assert any('номер партии' in clause for clause in constraints([rule])['semantic'])


def test_current_whole_style_rule_is_reviewed_only_for_simple_praise():
    rules = json.loads((Path(__file__).parent / 'fixtures/deduplicated_style_rules.json').read_text(encoding='utf-8'))
    review = SimpleNamespace(text='', rating=5, marketplace='ozon')
    answer = positive_template(review, [rules['review']], product=PRODUCT)
    assert answer
    assert not local_issues(answer, [rules['review']], review)
    assert positive_template(review, [rules['review'] + ' Также сообщай номер партии.'], product=PRODUCT) is None
    review.text = 'позже дам информацию по качеству'
    assert positive_template(review, [rules['review']], product=PRODUCT)


@pytest.mark.parametrize('rule', ['Одна благодарность.', 'Не повторяй благодарность и извинения.'])
def test_no_repeated_thanks_in_current_rule_wording(rule):
    assert local_issues('Здравствуйте. Спасибо за отзыв. Благодарим за покупку.', [rule])


@pytest.mark.parametrize('marketplace', ['wb', 'ozon', 'yandex'])
def test_saved_generation_and_regeneration_respect_deferred_experience(client, monkeypatch, marketplace):
    from app.db import Session, Memory, Product, Review, Action
    from app.integrations import ollama
    from app.services import generate_draft
    rules = json.loads((Path(__file__).parent / 'fixtures/untried_review_rules.json').read_text(encoding='utf-8'))
    checker = AsyncMock(side_effect=AssertionError('Exact audited neutral case needs no model'))
    monkeypatch.setattr(ollama, 'structured', checker)
    monkeypatch.setattr(ollama, 'reply_variants', checker)
    monkeypatch.setattr(ollama, 'chat', checker)
    with Session() as db:
        db.add_all([Memory(id='rule-'+str(i), kind='REVIEW_RULE', text=rule) for i, rule in enumerate(rules)])
        db.add(Product(id='eco', name=PRODUCT.name, category=PRODUCT.category))
        db.flush()
        db.add(Review(id='deferred', wb_review_id='deferred', product_id='eco', rating=5,
                      text='позже дам информацию по качеству', marketplace=marketplace, risk='NORMAL'))
        db.commit()
        for _ in range(3):
            result = asyncio.run(generate_draft(db, 'deferred'))
            assert result['quality']['passed'], result['quality']
            assert result['quality']['owner_rules']['passed']
            assert result['quality']['owner_rules']['verification_method'] == 'local_positive'
            assert 'впечатлениям после использования' in result['text']
            assert 'оценили качество' not in result['text']
            assert 'поделились опытом' not in result['text']
        assert not db.get(Review, 'deferred').is_answered
        assert not db.query(Action).all()
    checker.assert_not_awaited()


@pytest.mark.parametrize('change', ['unknown', 'edited', 'complaint', 'question', 'low_rating', 'manual', 'danger'])
def test_untried_fast_path_never_approves_unknown_rules_or_other_situations(change):
    rules = json.loads((Path(__file__).parent / 'fixtures/untried_review_rules.json').read_text(encoding='utf-8'))
    row = SimpleNamespace(text='позже дам информацию по качеству', rating=5, marketplace='yandex')
    if change == 'unknown':
        rules.append('Сообщай точный номер партии.')
    elif change == 'edited':
        rules[0] += ' Сообщай точный номер партии.'
    elif change == 'complaint':
        row.text += '. Но упаковка повреждена.'
    elif change == 'question':
        row.marketplace = 'yandex_question'
    elif change == 'low_rating':
        row.rating = 2
    elif change == 'manual':
        row.manual = True
    else:
        row.risk = 'HIGH'
    assert positive_template(row, rules, product=PRODUCT) is None
    assert not verified_positive_reply('Здравствуйте. Рады, что Вы оценили качество товара.', row, rules, product=PRODUCT)
