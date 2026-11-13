from uuid import uuid4
from types import SimpleNamespace
import pytest
from sqlalchemy import select, func
from app.db import Session, Memory, Action, Setting, Job, setting
from app.rule_duplicates import identity
from app.rule_cleanup import cleanup, content_hash
from app.owner_reply_rules import matching_owner_rules


def body(**changes):
    return {'text': 'Не обещай результат.', 'kind': 'REVIEW_RULE',
            'scope': 'brand:EIKOSHA', 'operation_id': str(uuid4()), **changes}


def test_new_operation_cannot_create_same_normalized_rule(client):
    original = body()
    first = client.post('/memory', json=original)
    assert first.status_code == 200
    assert client.post('/memory', json=original).json()['id'] == first.json()['id']
    for text in ('Не обещай результат.', ' НЕ обещай\n результат. '):
        duplicate = client.post('/memory', json=body(text=text, scope='brand:eikosha'))
        assert duplicate.status_code == 409, duplicate.text
        assert 'Дубль не сохранён' in duplicate.json()['detail']
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Memory)) == 1


def test_disabled_existing_is_not_recreated(client):
    first = client.post('/memory', json=body()).json()
    assert client.patch('/memory/'+first['id']+'/enabled', json={'enabled':False}).status_code == 200
    assert client.post('/memory', json=body()).status_code == 409
    assert client.patch('/memory/'+first['id']+'/enabled', json={'enabled':True}).status_code == 200


@pytest.mark.parametrize('changes', [
    {'kind':'QUESTION_RULE'}, {'scope':'brand:OTHER'}, {'text':'Обещай результат.'},
    {'text':'Не обещай результат!'}, {'text':'Не обещай результат за 15 минут.'}])
def test_different_audiences_scopes_and_meaning_are_preserved(client, changes):
    assert client.post('/memory', json=body()).status_code == 200
    assert client.post('/memory', json=body(**changes)).status_code == 200


def test_proposals_deduplicate_and_confirmation_rechecks_after_concurrent_create(client):
    proposal = {k:v for k,v in body().items() if k != 'operation_id'}
    pending = client.post('/memory/propose', json=proposal).json()
    assert client.post('/memory/propose', json={**proposal,'text':'Не  обещай\nрезультат.'}).json()['id'] == pending['id']
    assert client.post('/memory', json=body()).status_code == 200
    response = client.post('/actions/'+pending['id']+'/confirm', json={'manual_ack':False})
    assert response.status_code == 409, response.text
    assert client.post('/memory/propose', json=proposal).status_code == 409
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Memory)) == 1
        assert db.get(Action,pending['id']).status == 'pending'


def test_kind_change_and_enable_cannot_reintroduce_legacy_copy(client):
    with Session() as db:
        db.add_all([Memory(id='active',text='Не обещай результат.',kind='REVIEW_RULE',scope='global'),
                    Memory(id='copy',text='НЕ обещай  результат.',kind='REVIEW_RULE',scope='global',enabled=False),
                    Memory(id='question',text='Не обещай результат.',kind='QUESTION_RULE',scope='global')])
        db.commit()
    assert client.patch('/memory/copy/enabled', json={'enabled':True}).status_code == 409
    assert client.patch('/memory/question/kind', json={'kind':'REVIEW_RULE'}).status_code == 409
    with Session() as db:
        assert not db.get(Memory,'copy').enabled
        assert db.get(Memory,'question').kind == 'QUESTION_RULE'


def test_cleanup_is_dry_run_recoverable_and_idempotent(client):
    with Session() as db:
        db.add_all([Memory(id='a',text='Не обещай результат.',kind='REVIEW_RULE',scope='global'),
                    Memory(id='b',text='НЕ обещай  результат.',kind='REVIEW_RULE',scope='global'),
                    Memory(id='q',text='Не обещай результат.',kind='QUESTION_RULE',scope='global')])
        db.commit()
        assert len(cleanup(db)['copies']) == 1
        assert db.get(Memory,'b').enabled
        result = cleanup(db,apply=True)
        backup = setting(db,result['backup'],{})
        assert len(backup['rules']) == 3
        assert result['active_after'] == 2
        assert db.get(Memory,'a').enabled and db.get(Memory,'q').enabled
        assert not db.get(Memory,'b').enabled
        assert setting(db,'memory-meta:b',{})['duplicate_of'] == 'a'
        assert cleanup(db,apply=True)['copies'] == []
    assert client.patch('/memory/b/enabled', json={'enabled':True}).status_code == 409
    # Removing the canonical entry allows a new rule; retired history is not a
    # permanent tombstone preventing legitimate new work.
    assert client.delete('/memory/a').status_code == 200
    assert client.post('/memory',json=body(scope='global')).status_code == 200


def test_reviewed_coverage_checks_full_text_and_keeps_backup(client):
    with Session() as db:
        general = Memory(id='g',text='Учитывай каждое замечание и вопрос. Не выдумывай причины.',kind='REVIEW_RULE',scope='global')
        specific = Memory(id='p',text='Учитывай каждое замечание и вопрос.',kind='REVIEW_RULE',scope='product:p')
        db.add_all([general,specific]);db.commit()
        key=lambda row: {'kind':row.kind,'scope':row.scope,'sha256':content_hash(row)}
        plan=[{'source':key(specific),'target':key(general)}]
        bad=[{'source':key(specific),'target':{**key(general),'sha256':'old'}}]
        with pytest.raises(ValueError,match='changed'):
            cleanup(db,apply=True,covered_plan=bad)
        db.rollback()
        assert db.get(Memory,'p').enabled
        assert db.scalar(select(func.count()).select_from(Setting).where(Setting.key.like('rule-dedup-backup:%'))) == 0
        result=cleanup(db,apply=True,covered_plan=plan)
        assert result['active_after']==1 and db.get(Memory,'p').text==specific.text
        assert cleanup(db,apply=True,covered_plan=plan)['copies']==[]
    assert client.patch('/memory/p/enabled',json={'enabled':True}).status_code==409
    assert client.post('/memory',json=body(text=specific.text,scope='product:p')).status_code==409


def test_generation_applies_normalized_copies_once_without_merging_different_rules(client):
    with Session() as db:
        rows=[Memory(id='g',text='Не обещай результат.',kind='REVIEW_RULE',scope='global'),
              Memory(id='b',text='НЕ обещай\n результат.',kind='REVIEW_RULE',scope='brand:EIKOSHA'),
              Memory(id='c',text='Не обещай результат за 15 минут.',kind='REVIEW_RULE',scope='global')]
        result=matching_owner_rules(db,SimpleNamespace(id='p',brand='EIKOSHA',category=''),rule_rows=rows)
        assert result == [rows[2].text,rows[1].text]
        assert identity(rows[0]) != identity(rows[1])


def test_automatic_learning_reuses_audited_global_coverage_without_new_rules_or_jobs(monkeypatch):
    from app import library_workspace as library
    from app.db import set_setting
    with Session() as db:
        rule=Memory(id='general',text='Учитывай каждое замечание и вопрос.',kind='REVIEW_RULE',scope='global')
        db.add(rule);set_setting(db,'auto_case_rules_enabled',True);db.commit()
        monkeypatch.setattr(library,'CASE_COVERAGE',{content_hash(rule):[value for _,value in library.GUIDANCE]+[library.DEFAULT_GUIDANCE]})
        for pid in ('p1','p2','p3'):
            row=SimpleNamespace(status='attention',issues=['ответить именно на содержание отзыва'])
            assert library.suggest_case_rule(db,row,SimpleNamespace(id=pid,product_id=pid)).id=='general'
        assert db.scalar(select(func.count()).select_from(Memory))==1
        assert db.scalar(select(func.count()).select_from(Job))==0
        # Identical wording in questions, or an edited/disabled review policy,
        # does not prove that the review guidance is currently covered.
        for kind,enabled,text in [('QUESTION_RULE',True,rule.text),('REVIEW_RULE',False,rule.text),('REVIEW_RULE',True,'Changed requirement')]:
            rule.kind,rule.enabled,rule.text=kind,enabled,text;db.flush()
            assert library.covered_case_rule(db,[library.GUIDANCE[0][1]]) is None


def test_automatic_learning_respects_a_manually_created_same_rule():
    from app import library_workspace as library
    from app.db import set_setting
    with Session() as db:
        row=SimpleNamespace(status='attention',issues=['ответить именно на содержание отзыва'])
        text='Разбор сложного отзыва. '+' '.join(library.guidance(row.issues))
        db.add(Memory(id='manual',text=text,kind='REVIEW_RULE',scope='product:p',enabled=False))
        set_setting(db,'auto_case_rules_enabled',True);db.commit()
        result=library.suggest_case_rule(db,row,SimpleNamespace(id='r',product_id='p'))
        assert result.id=='manual' and not result.enabled
        assert db.scalar(select(func.count()).select_from(Memory))==1
