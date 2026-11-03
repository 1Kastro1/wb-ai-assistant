"""Regression cases from real marketplace drafts, shared by all three shops."""
from types import SimpleNamespace
import json
from pathlib import Path

import pytest

from app.product_guidance import positive_detail, positive_review_reply
from app.reply_policy import semantic_reply_issues
from app.owner_reply_policy import constraints, local_issues, apply_local_preferences
from app.rule_execution import positive_template
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
    assert positive_template(review, [rules['review']], product=PRODUCT) is None


@pytest.mark.parametrize('rule', ['Одна благодарность.', 'Не повторяй благодарность и извинения.'])
def test_no_repeated_thanks_in_current_rule_wording(rule):
    assert local_issues('Здравствуйте. Спасибо за отзыв. Благодарим за покупку.', [rule])
