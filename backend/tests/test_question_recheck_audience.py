import asyncio
import pytest
from app.db import Session, Draft, Review, Memory, Job
from app.draft_audit import recheck_step


@pytest.mark.parametrize('marketplace', ['wb_question', 'ozon_question', 'yandex_question'])
def test_question_recheck_never_uses_review_rules_or_review_score(client, seeded, monkeypatch, marketplace):
    from app import services, owner_reply_policy
    with Session() as db:
        row = db.get(Review, 'r1')
        row.marketplace, row.text, row.rating = marketplace, 'Есть ли инструкция?', 0
        row.manual, row.risk = False, 'NORMAL'
        db.add_all([Memory(id='review-only', kind='REVIEW_RULE', text='Подпись для отзывов'),
                    Memory(id='question-only', kind='QUESTION_RULE', text='Правило для вопросов')])
        db.add(Draft(id='question-draft', review_id='r1', text='Ответ на вопрос.', original='Ответ на вопрос.'))
        db.add(Job(id='question-recheck', kind='draft_recheck', result={'draft_ids':['question-draft']}))
        db.commit()
    def assess(*args, owner_rules=(), **kwargs):
        assert owner_rules == ['Правило для вопросов']
        return {'score': 96, 'passed': True, 'issues': [], 'evaluator_version': 'question-check'}
    def wrong(*args, **kwargs):
        raise AssertionError('Review scoring used for a question')
    async def enforce(answer, review, product, rules, facts, evaluator, **kwargs):
        assert rules == ['Правило для вопросов']
        assert evaluator(answer)['passed']
        return answer, {'passed':True, 'checked':True, 'matched':1, 'issues':[]}
    monkeypatch.setattr(services, 'assess_question_answer', assess)
    monkeypatch.setattr(services, 'assess_reply', wrong)
    monkeypatch.setattr(owner_reply_policy, 'enforce_owner_rules', enforce)
    asyncio.run(recheck_step('question-recheck'))
    with Session() as db:
        assert db.get(Job, 'question-recheck').status == 'completed'
        assert db.get(Draft, 'question-draft').quality['passed']
        assert db.get(Draft, 'question-draft').text == 'Ответ на вопрос.'
