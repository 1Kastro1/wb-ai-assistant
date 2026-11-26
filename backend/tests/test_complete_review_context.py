import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.db import Product, Review
from app.integrations import ollama
from app.owner_reply_policy import enforce_owner_rules
from app.reply_policy import semantic_reply_issues
from app.review_context import context_current
from app.review_sections import wb_review_context
from app.safety import rule_reply


@pytest.mark.parametrize('marketplace', ['wb', 'ozon', 'yandex'])
def test_verdict_is_bound_to_latest_complete_message(monkeypatch, marketplace):
    review = Review(id='r', rating=5, marketplace=marketplace, text='Поначалу запах был слабым.',
                    sections={'supplement': 'Переставил ароматизатор, и запах стал заметнее.'})
    product = Product(name='Ароматизатор', brand='EIKOSHA', category='ароматизаторы')
    answer = 'Здравствуйте! Рады, что после перестановки аромат стал заметнее.'
    check = AsyncMock(return_value={'compliant': True, 'violations': [], 'revised_answer': ''})
    monkeypatch.setattr(ollama, 'structured', check)

    _, verdict = asyncio.run(enforce_owner_rules(answer, review, product, [], [],
        lambda _: {'passed': True}, check_only=True))
    payload = json.loads(check.await_args.args[0][-1]['content'])
    assert payload['buyer_message'] == review.text + '\n' + review.sections['supplement']
    assert verdict['passed']
    assert context_current(review, answer, verdict)
    assert not context_current(review, answer + ' Спасибо!', verdict)
    review.sections = {'supplement': 'Через два дня аромат исчез.'}
    assert not context_current(review, answer, verdict)


@pytest.mark.parametrize('is_supplement', [False, True])
def test_wb_keeps_original_and_linked_update_in_chronological_order(is_supplement):
    original = {'id': 'old', 'text': 'Сначала слабо пахло.', 'childFeedbackId': 'new'}
    update = {'id': 'new', 'pros': 'Запах стал заметнее', 'text': 'Переставил на дефлектор.', 'parentFeedbackId': 'old'}
    get_linked = AsyncMock(return_value=original if is_supplement else update)
    message, sections = asyncio.run(wb_review_context(update if is_supplement else original, get_linked))
    get_linked.assert_awaited_once_with('old' if is_supplement else 'new')
    assert message == 'Сначала слабо пахло.\nДополнение покупателя: Переставил на дефлектор.\nЗапах стал заметнее'
    assert sections['supplement'] == 'Переставил на дефлектор.\nЗапах стал заметнее'


@pytest.mark.parametrize(('message', 'bad', 'good'), [
    ('Заказывала один запах, пришел другой. Дома вскрыла и поняла.',
     'Скорее всего, дело в условиях хранения. Мы sincerely поможем с заменой.',
     'Извините, что пришёл другой аромат. По полученному заказу можно обратиться в поддержку маркетплейса.'),
    ('Сначала аромат был едва уловимый. Перевернул открытой стороной к дефлектору, аромат стал явный.',
     'Сожалеем, что аромат ощущается слабее, чем Вы ожидали. Попробуйте разместить его на дефлекторе.',
     'Рады, что после изменения положения аромат стал заметнее.'),
    ('Раньше легко хватало на 6 месяцев. Сейчас ну три.',
     'Нам жаль, что запах Вам не подошёл.',
     'Жаль, что в этот раз аромат держался меньше, чем раньше.'),
    ('Запах приятный, мне даже показалось, что его слишком много.',
     'Рады, что аромат Вам понравился.',
     'Спасибо за отзыв! Понимаем, что аромат показался слишком сильным.'),
])
def test_context_rejects_published_regressions(message, bad, good):
    review = SimpleNamespace(rating=5, text=message, marketplace='wb')
    product = Product(name='Ароматизатор', brand='EIKOSHA', category='')
    assert semantic_reply_issues(review, product, bad)
    assert not semantic_reply_issues(review, product, good)


def test_rating_only_templates_are_short_and_unique():
    product = Product(name='Ароматизатор', brand='EIKOSHA', category='')
    replies = []
    for rating in (2, 5):
        review = SimpleNamespace(id='one', rating=rating, text='', marketplace='wb', risk='NORMAL')
        replies.extend(rule_reply(review, product, revision) for revision in range(3))
    assert len(set(replies)) == len(replies) == 6
    assert max(map(len, replies)) < 110
