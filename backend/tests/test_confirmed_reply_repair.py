import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.confirmed_reply_repair import ADAPTER, RATING, confirmed_issue, repair
from app.services import assess_question_answer, assess_reply


@pytest.mark.parametrize(('market', 'rating', 'message', 'before', 'product_name', 'defect', 'detail'), [
    ('ozon', 1, 'Спустя две недели щётка трёт полосами.', 'Добрый день! Спасибо за высокую оценку!',
     'Щётка стеклоочистителя 400 мм', RATING, 'через две недели'),
    ('yandex_question', 0, 'Добрый день! Есть переходник на Valt 5.1?',
     'Добрый день! Спасибо за вопрос. Недостаточно подтверждённых данных.',
     'Зимняя щётка 400 мм', ADAPTER, 'Valt 5.1'),
])
def test_confirmed_defect_is_repaired_and_exact_new_text_checked(
        monkeypatch, market, rating, message, before, product_name, defect, detail):
    review = SimpleNamespace(marketplace=market, rating=rating, text=message,
                             risk='NORMAL', manual=False, safety_critical=False)
    product = SimpleNamespace(name=product_name, brand='Тест', category='', part_number='', facts=[])
    assess = (lambda answer: assess_question_answer(review, product, answer)) if not rating else (
        lambda answer: assess_reply(review, product, answer, []))

    async def check(candidate, *_args, check_only, group_limit):
        assert check_only and group_limit == 1 and candidate != before and detail in candidate
        return candidate, {'passed': True, 'checked': True, 'issues': [], 'proof': 'fresh'}

    checker = AsyncMock(side_effect=check)
    monkeypatch.setattr('app.confirmed_reply_repair.enforce_owner_rules', checker)
    assert confirmed_issue(review, before) == defect
    old_quality = {'passed': True, 'owner_rules': {'proof': 'old'}}
    after, quality = asyncio.run(repair(review, product, before, old_quality, [], [], assess))
    assert not confirmed_issue(review, after)
    assert quality['passed'] and quality['owner_rules']['proof'] == 'fresh'
    assert quality['confirmed_repair'] == {'issue': defect, 'status': 'rechecked'}
    assert checker.await_count == 1

    checker.side_effect = lambda candidate, *_args, **_opts: (
        candidate, {'passed': True, 'checked': False, 'issues': []})
    unchanged, blocked = asyncio.run(repair(review, product, before, old_quality, [], [], assess))
    assert unchanged == before and not blocked['passed']
    assert blocked['confirmed_repair']['status'] == 'blocked'


def test_observation_and_protected_answers_do_not_trigger_unverified_rewrites(monkeypatch):
    review = SimpleNamespace(marketplace='wb', rating=5, text='Всё устроило')
    product = SimpleNamespace(name='Щётка')
    text = 'Добрый день! Спасибо за высокую оценку!'
    quality = {'passed': True, 'reply_supervisor': {'flags': ['unsupported_claim']}}
    check = AsyncMock()
    monkeypatch.setattr('app.confirmed_reply_repair.enforce_owner_rules', check)
    assert asyncio.run(repair(review, product, text, quality, [], [], None)) == (text, quality)
    review.rating = 1
    reply, result = asyncio.run(repair(review, product, text, quality, [], [], None, protected=True))
    assert reply == text and not result['passed']
    check.assert_not_awaited()


@pytest.mark.parametrize('deferred', [False, True])
def test_saved_question_recheck_replaces_only_the_checked_revision(client, monkeypatch, deferred):
    from app.db import Draft, Job, Product, Review, Session
    from app.draft_audit import recheck_step
    from app.rule_retry import enqueue
    from app.services import propose_publish

    if deferred:
        async def start(candidate, *_args, **_opts):
            return candidate, {'passed': False, 'checked': False, 'continuation': True,
                               'group_progress': {'completed': [0]}, 'issues': ['Проверка продолжается']}
        monkeypatch.setattr('app.confirmed_reply_repair.enforce_owner_rules', start)

        async def finish(candidate, *_args, previous_check, **_opts):
            assert previous_check['group_progress']['completed'] == [0]
            return candidate, {'passed': True, 'checked': True, 'issues': []}
        monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules', finish)

    text = 'Добрый день! Спасибо за вопрос. Недостаточно подтверждённых данных.'
    with Session() as db:
        db.add(Product(id='adapter', name='Зимняя щётка 400 мм'))
        db.flush()
        question = Review(id='adapter', wb_review_id='adapter', wb_account_id='owner', product_id='adapter',
                          marketplace='yandex_question', text='Есть переходник на Valt 5.1?',
                          rating=0, risk='NORMAL', status='draft_ready')
        db.add(question)
        db.flush()
        draft = Draft(id='adapter', review_id=question.id, text=text, original=text, revision=1,
                      quality={'passed': True, 'reply_supervisor': {'status': 'attention'}})
        db.add(draft)
        with pytest.raises(ValueError, match='подтверждённую ошибку'):
            propose_publish(db, [question.id])
        job = enqueue(db, draft, question, confirmed_repair=True)
        db.commit()
        job_id = job.id
    asyncio.run(recheck_step(job_id))
    if deferred:
        with Session() as db:
            draft = db.get(Draft, 'adapter')
            assert draft.revision == 2 and 'Valt 5.1' in draft.text
            assert draft.quality['confirmed_repair']['status'] == 'pending'
            assert db.get(Job, job_id).result['repair_revision'] == 2
            with pytest.raises(ValueError, match='ещё не прошёл'):
                propose_publish(db, ['adapter'])
        asyncio.run(recheck_step(job_id))
    with Session() as db:
        draft = db.get(Draft, 'adapter')
        assert draft.revision == 2 and draft.original == text and 'Valt 5.1' in draft.text
        assert draft.edits[-1]['reason'] == 'confirmed_reply_repair'
        assert draft.quality['owner_rules']['checked'] and draft.quality['passed']
        assert 'reply_supervisor' not in draft.quality
        assert db.get(Job, job_id).status == 'completed'
        result = propose_publish(db, ['adapter'])
        assert result['payload']['items'][0]['revision'] == 2
