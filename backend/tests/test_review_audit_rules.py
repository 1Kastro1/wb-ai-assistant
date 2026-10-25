import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as S
from unittest.mock import AsyncMock
import pytest
from app.rule_execution import digest, positive_template, audited_case_template, select_rules
from app.owner_reply_policy import enforce_owner_rules
from app.integrations import ollama
from app.services import assess_reply

FIXTURES = Path(__file__).parent / 'fixtures'
UPDATES = json.loads((FIXTURES / 'review_audit_rules.json').read_text(encoding='utf-8'))


def rules():
    base = [row['text'] for row in json.loads((FIXTURES / 'positive_rating_rules.json').read_text(encoding='utf-8'))]
    replacements = {row['previous_digest']: row['text'] for row in UPDATES if row.get('previous_digest')}
    return list(dict.fromkeys([*(replacements.get(digest(text), text) for text in base), *(row['text'] for row in UPDATES)]))


@pytest.mark.parametrize('rating', [4, 5])
def test_saved_audit_rules_keep_blank_ratings_local_and_do_not_call_model(monkeypatch, rating):
    model = AsyncMock(side_effect=AssertionError('empty ratings must stay local'))
    monkeypatch.setattr(ollama, 'structured', model)
    r = S(id='rating', text='', rating=rating, risk='NORMAL', marketplace='yandex', manual=False)
    p = S(name='Фильтр', brand='TEST', category='', facts=[])
    for revision in range(3):
        answer = positive_template(r, rules(), revision, product=p)
        assert answer
        reply, verdict = asyncio.run(enforce_owner_rules(answer, r, p, rules(), [], lambda text:assess_reply(r,p,text), allow_fast_positive=True))
        assert verdict['passed'] and verdict['verification_method']=='local_positive'
    model.assert_not_awaited()


def test_edit_to_the_audited_rule_requires_verification_again():
    r = S(id='rating', text='', rating=5, risk='NORMAL', marketplace='yandex', manual=False)
    p = S(name='Фильтр',brand='TEST',category='')
    changed = rules()+[UPDATES[-2]['text']+' Всегда назови точный номер партии.']
    assert positive_template(r,changed,product=p) is None


def test_resolved_sprayer_still_qualifies_but_new_questions_do_not():
    r = S(text='Очищает хорошо, разводов практически нет. Пришло с нерабочим распылителем, флакон протёк. Упакован отвратительно! Поменял на новый, всё работает.',rating=4,risk='NORMAL',marketplace='yandex',manual=False,safety_critical=False)
    p = S(name='Очиститель Fine Glass',brand='KANGAROO',category='',facts=[])
    answer = audited_case_template(r,p,rules())
    assert answer and 'уже заменили' in answer
    assert not any(word in answer.lower() for word in ('фотограф','через полученный заказ'))
    r.text += ' Можно использовать на мраморе?'
    assert audited_case_template(r,p,rules()) is None


def test_workflow_rule_is_not_passed_as_customer_reply_guidance():
    workflow=next(r['text'] for r in UPDATES if r['name']=='verification')
    selected, skipped=select_rules([workflow],S(text='Не подошло',rating=2),S(name='Фильтр',category=''))
    assert not selected and skipped
