import json
from pathlib import Path
import pytest
from sqlalchemy import select
from app import owner_policy_templates as policy, owner_policy_cleanup as cleanup, rule_execution
from app.db import Session, User, Memory, Job


def seed(db):
    old=json.loads((Path(policy.__file__).with_name('owner_policy_cleanup.json')).read_text(encoding='utf-8'))
    concise=json.loads((Path(policy.__file__).with_name('owner_policy_concise.json')).read_text(encoding='utf-8'))
    sources=[concise['changes'][1],old['changes'][3],next(c for c in old['changes'] if c['text'].startswith('Рекомендации для отзывов — Средства для кузова'))]
    for i,c in enumerate(sources): db.add(Memory(id='source-'+str(i),kind='REVIEW_RULE',text=c['text'],scope='global'))
    db.add(Memory(id='question',kind='QUESTION_RULE',text='Не изменять вопросы',scope='global'))
    db.commit()
    return db.scalar(select(User).where(User.role=='owner')).id


def test_rule_backup_profiles_idempotence_and_question_preservation(client,tmp_path):
    with Session() as db:
        actor=seed(db)
        result=policy.apply(db,actor,tmp_path)
        assert result['created']==3 and result['archived']==3
        assert len(json.loads(Path(result['backup']).read_text(encoding='utf-8'))['rules'])==3
        assert db.get(Memory,'question').enabled and db.get(Memory,'question').text=='Не изменять вопросы'
        for c in policy.POLICY['changes']: assert rule_execution.profile(c['text']).get('local_positive') is True
        assert not any((j.result or {}).get('policy_scan') for j in db.scalars(select(Job)))
        assert policy.apply(db,actor,tmp_path)['changed']==0


def test_updated_owner_rule_cannot_be_overwritten(client,tmp_path):
    with Session() as db:
        actor=seed(db); db.get(Memory,'source-0').text+=' Правка владельца.';db.commit()
        with pytest.raises(ValueError,match='изменилось'): policy.apply(db,actor,tmp_path)
        assert db.get(Memory,'source-1').enabled
