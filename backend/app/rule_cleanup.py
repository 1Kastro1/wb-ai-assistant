"""Read-only audit by default; recoverably retire exact or reviewed duplicates."""
import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select
from .db import Session, Memory, setting, set_setting, now
from .rule_duplicates import identity, lock_rules, normalized_scope


def content_hash(rule):
    return hashlib.sha256(rule.text.encode('utf-8')).hexdigest()


def cleanup(db, *, apply=False, covered_plan=(), actor_id=''):
    if apply:
        lock_rules(db, begin=True)
    rows = list(db.scalars(select(Memory).order_by(Memory.id)))
    groups = defaultdict(list)
    for row in rows:
        if not setting(db, 'memory-meta:' + row.id, {}).get('duplicate_of'):
            groups[identity(row)].append(row)
    changes = {}
    for group in groups.values():
        canonical, *copies = sorted(group, key=lambda row: (not row.enabled, row.id))
        for copy in copies:
            changes[copy.id] = (copy, canonical, 'exact')
    # Semantic removal is deliberately limited to an explicitly reviewed plan.
    # Hash both full texts; refuse the whole operation if either changed.
    for plan in covered_plan:
        def find(key):
            found = [row for row in rows if row.kind == key['kind']
                     and normalized_scope(row.scope) == normalized_scope(key['scope'])
                     and content_hash(row) == key['sha256']]
            if len(found) != 1:
                raise ValueError('Reviewed rules changed; no cleanup applied')
            return found[0]
        copy, canonical = find(plan['source']), find(plan['target'])
        if (copy.id == canonical.id or not canonical.enabled or copy.kind != canonical.kind
                or normalized_scope(canonical.scope) not in ('global', normalized_scope(copy.scope))):
            raise ValueError('Invalid reviewed rule coverage; no cleanup applied')
        meta = setting(db, 'memory-meta:' + copy.id, {})
        if meta.get('duplicate_of') == canonical.id and not copy.enabled:
            continue
        if not copy.enabled or meta.get('duplicate_of'):
            raise ValueError('Reviewed rule state changed; no cleanup applied')
        changes[copy.id] = (copy, canonical, 'covered')
    result = {'total': len(rows), 'active_before': sum(bool(r.enabled) for r in rows),
              'copies': [{'id': row.id, 'canonical_id': target.id, 'reason': reason,
                         'was_enabled': bool(row.enabled)} for row, target, reason in changes.values()]}
    if apply and changes:
        from .rule_change_recheck import rule_changed, snapshot
        backup = 'rule-dedup-backup:' + uuid4().hex
        set_setting(db, backup, {'at': now(), 'actor_id': actor_id,
            'rules': [snapshot(row) | {'meta': setting(db, 'memory-meta:' + row.id, {})} for row in rows]})
        for row, target, reason in changes.values():
            before = snapshot(row)
            row.enabled = False
            set_setting(db, 'memory-meta:' + row.id, {
                **setting(db, 'memory-meta:' + row.id, {}), 'duplicate_of': target.id,
                'duplicate_reason': reason, 'duplicate_archived_at': now(),
                'duplicate_original_enabled': bool(before['enabled'])})
            rule_changed(db, before, snapshot(row), actor_id)
        db.commit()
        result['backup'] = backup
    result['active_after'] = sum(bool(r.enabled) for r in rows)
    result['applied'] = apply
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--covered-plan', type=Path)
    parser.add_argument('--actor', default='')
    args = parser.parse_args()
    plan = json.loads(args.covered_plan.read_text(encoding='utf-8')) if args.covered_plan else []
    with Session() as db:
        print(json.dumps(cleanup(db, apply=args.apply, covered_plan=plan, actor_id=args.actor), ensure_ascii=True))
