import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from app.db import Session, Memory, User, Product, Review, Draft, Job
from app import owner_policy_cleanup as cleanup, rule_execution as execution
from app.owner_reply_rules import review_signature, matching_owner_rules
from app.draft_audit import recheck_step


def old_rules():
    # Portable fixtures are copied from the exact rendered owner rules.
    return json.loads((Path(__file__).parent / 'fixtures/cleanup_policy_before.json').read_text(encoding='utf-8'))


def seed():
    with Session() as db:
        db.add(User(id='cleanup-owner', username='cleanup-owner', display_name='Owner', password_hash='unused', role='owner'))
        for i, row in enumerate(old_rules()):
            db.add(Memory(id='old-'+str(i), kind=row['kind'], text=row['text'], scope='global'))
        db.add(Product(id='p', name='Очиститель Profoam 4000', brand='Kangaroo'))
        db.flush()
        for i, answered in enumerate((False, True)):
            db.add(Review(id='r'+str(i), wb_review_id='r'+str(i), product_id='p', text='не чистит', rating=1,
                          wb_account_id='shop', is_answered=answered))
        db.flush()
        db.add(Draft(id='d', review_id='r0', text='Сохранённый текст', original='Сохранённый текст', revision=8))
        db.add(Draft(id='published', review_id='r1', text='Отправленный текст', original='Отправленный текст'))
        db.commit()


def test_exact_cleanup_backup_idempotence_and_no_generation(client, tmp_path):
    seed()
    with Session() as db:
        assert len(cleanup.plan(db)) == 34
        result = cleanup.apply(db, 'cleanup-owner', tmp_path)
        assert result['archived'] == 50 and result['created'] == 34
        snapshot = json.loads(Path(result['backup']).read_text(encoding='utf-8'))
        assert len(snapshot['rules']) == 50
        rules = list(db.scalars(select(Memory).where(Memory.enabled == True)))
        assert sum(r.kind == 'REVIEW_RULE' for r in rules) == 20
        assert sum(r.kind == 'QUESTION_RULE' for r in rules) == 21
        job = db.get(Job, result['verification_jobs'][0])
        assert job.result['total'] == 1 and job.result['check_only'] and job.result['policy_scan']
        assert db.get(Draft, 'd').revision == 8
        assert db.get(Draft, 'd').text == 'Сохранённый текст'
        assert db.get(Draft, 'published').text == 'Отправленный текст'
        assert cleanup.apply(db, 'cleanup-owner', tmp_path)['changed'] == 0
        assert len(list(db.scalars(select(Job)))) == 1
        rules[0].enabled = False; db.commit()
        assert cleanup.apply(db, 'cleanup-owner', tmp_path)['changed'] == 0
        assert not rules[0].enabled


def test_cleanup_aborts_on_owner_edit_and_rejects_non_owner(client, tmp_path):
    seed()
    with Session() as db:
        with pytest.raises(ValueError, match='владельцу'):
            cleanup.apply(db, 'absent', tmp_path)
        target = next(r for r in db.scalars(select(Memory)) if execution.digest(r.text) in cleanup.POLICY['changes'][0]['old_hashes'])
        target.text += ' Изменение владельца.'; db.commit()
        with pytest.raises(ValueError, match='изменилось'):
            cleanup.apply(db, 'cleanup-owner', tmp_path)
        assert all(r.enabled for r in db.scalars(select(Memory)))
        assert not list(db.scalars(select(Job)))


@pytest.mark.parametrize('text', [
    'Не благодари за отзыв. Подпись «С уважением ГК Юником.» к вопросам не добавляй.',
    'Для вопросов не добавляй подпись «С уважением ГК Юником.», как в ответе на отзыв.',
])
def test_negative_signature_instruction_remains_in_question_policy(client, text):
    assert review_signature(text) == ''
    with Session() as db:
        db.add(Memory(id='q', kind='QUESTION_RULE', text=text)); db.commit()
        assert matching_owner_rules(db, Product(id='temporary', name='Товар', brand='', category=''), for_questions=True) == [text]
    assert review_signature('В конце ответа на отзыв добавляй подпись «С уважением ГК Юником.».') == 'С уважением ГК Юником.'


def test_compact_policy_filters_only_reviewed_category_blocks():
    row = SimpleNamespace(text='Не чистит', rating=1, marketplace='wb', risk='NORMAL', manual=False)
    product = SimpleNamespace(name='Очиститель Profoam 4000', category='')
    rules = [c['text'] for c in cleanup.POLICY['changes'] if c['kind'] == 'REVIEW_RULE']
    selected, skipped = execution.select_rules(rules, row, product)
    assert any(execution.profile(t).get('family') == 'cleaners' for t in selected)
    assert not any(t.startswith('Тормозные') or t.startswith('Щётки') for t in selected)
    assert len(selected) < len(rules) and skipped
    unknown = 'Щётки: новое правило владельца без профиля.'
    assert unknown in execution.select_rules([unknown], row, product)[0]
    product.name = 'Товар'; row.text = ''
    assert execution.select_rules(rules, row, product)[0] == rules


def test_cleanup_retains_strict_audited_routes_and_unknown_policy_opt_out(client, tmp_path):
    from test_known_complaint_rules import BUYER
    seed()
    with Session() as db:
        cleanup.apply(db, 'cleanup-owner', tmp_path)
        product = db.get(Product, 'p')
        product.name = 'Очиститель стёкол Fine Glass'
        row = db.get(Review, 'r0'); row.text = BUYER; row.rating = 4
        row.risk = 'NORMAL'; row.manual = False; row.safety_critical = False
        rules = matching_owner_rules(db, product)
        answer = execution.audited_case_template(row, product, rules)
        assert answer and 'уже заменили' in answer
        assert answer.endswith('С уважением ГК Юником.')
        assert execution.audited_case_template(row, product, rules + ['Всегда указывай номер партии.']) is None
        row.text = ''; row.rating = 5
        assert execution.positive_template(row, rules, product=product)


def test_policy_scan_preserves_edits_ignores_published_and_resumes(client, monkeypatch):
    seed()
    from app import services, owner_reply_policy
    with Session() as db:
        db.add(Job(id='scan', kind='draft_recheck', result={'account_id':'shop', 'policy_scan':True,
                    'check_only':True, 'total':1, 'cursor_id':''}))
        draft = db.get(Draft, 'd')
        draft.edits = [{'by':'employee', 'old':'old', 'new':draft.text}]
        draft.quality = {'passed':False, 'text_issues':['старое замечание'], 'owner_rules':{'passed':False}}
        db.commit()
    monkeypatch.setattr(services, 'assess_reply', lambda *a, **kw: {'passed':True, 'issues':[], 'score':99})
    async def check(answer, *args, **kwargs):
        assert kwargs['check_only'] and kwargs['check_seconds'] == 45
        return answer, {'passed':True, 'checked':True, 'matched':0, 'issues':[], 'violations':[]}
    monkeypatch.setattr(owner_reply_policy, 'enforce_owner_rules', check)
    asyncio.run(recheck_step('scan'))
    with Session() as db:
        draft, job = db.get(Draft,'d'), db.get(Job,'scan')
        assert draft.text == 'Сохранённый текст' and draft.revision == 8 and len(draft.edits) == 1
        assert draft.quality['passed'] and not draft.quality.get('text_issues')
        assert job.status == 'completed' and job.result['checked'] == 1 and job.result['passed'] == 1
        assert db.get(Draft,'published').quality == {}


def test_policy_scan_cancellation_during_check_does_not_approve(client, monkeypatch):
    seed()
    from app import services, owner_reply_policy
    with Session() as db:
        db.add(Job(id='scan', kind='draft_recheck', result={'account_id':'shop','policy_scan':True,'check_only':True,'total':1}))
        db.commit()
    monkeypatch.setattr(services, 'assess_reply', lambda *a, **kw: {'passed':True, 'issues':[]})
    async def check(answer,*args,**kw):
        with Session() as db:
            db.get(Job,'scan').cancel_requested=True; db.commit()
        return answer, {'passed':True,'checked':True,'issues':[]}
    monkeypatch.setattr(owner_reply_policy,'enforce_owner_rules',check)
    asyncio.run(recheck_step('scan'))
    with Session() as db:
        assert db.get(Job,'scan').status == 'cancelled'
        assert db.get(Draft,'d').quality == {}
