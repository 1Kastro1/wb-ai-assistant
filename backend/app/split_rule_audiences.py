"""Explicit, idempotent migration of old common rules into independent copies.

Run with --apply after deploying the audience-aware application. No schema change.
"""
import json
import sys
from uuid import uuid4
from sqlalchemy import select
from .db import Memory, Session, setting, set_setting, now
from .owner_reply_rules import review_signature


def split_legacy_rules(db):
    changes = []
    for rule in list(db.scalars(select(Memory).where(Memory.kind == 'GLOBAL_RULE').with_for_update())):
        old = {'id': rule.id, 'kind': rule.kind}
        if rule.text.startswith('При вопросе с VIN или Frame сначала проверяй автомобиль через поле') and 'Uniqom' in rule.text:
            rule.kind = 'QUESTION_RULE'
        elif review_signature(rule.text):
            rule.kind = 'REVIEW_RULE'
        else:
            # A previous common rule keeps its behavior in both sections, but
            # subsequent enabling/moving/deleting is independent in each one.
            clone = Memory(id=str(uuid4()), text=rule.text, kind='QUESTION_RULE',
                           scope=rule.scope, enabled=rule.enabled)
            db.add(clone)
            meta = setting(db, 'memory-meta:' + rule.id, {})
            set_setting(db, 'memory-meta:' + clone.id, {**meta, 'copied_from_rule': rule.id})
            old['question_copy_id'] = clone.id
            rule.kind = 'REVIEW_RULE'
        changes.append({**old, 'new_kind': rule.kind})
    if changes:
        history = setting(db, 'rule-audience-migration:v1', [])
        set_setting(db, 'rule-audience-migration:v1', history + [{'at': now(), 'changes': changes}])
    return changes


if __name__ == '__main__':
    with Session() as db:
        changes = split_legacy_rules(db)
        if '--apply' in sys.argv:
            db.commit()
        else:
            db.rollback()
        print(json.dumps({'applied': '--apply' in sys.argv, 'rules_split': len(changes),
                          'question_copies': sum('question_copy_id' in change for change in changes)}))
