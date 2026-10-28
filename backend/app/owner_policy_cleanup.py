"""Apply the owner's reviewed rule cleanup, with a reversible rules-only backup.

Run --plan first. Exact whole-text hashes prevent overwriting later user edits.
Old rules are archived, never deleted. Saved replies are only rechecked.
"""
import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import select, func
from .config import DATA
from .db import Session, Memory, Setting, User, Job, Review, Draft, Activity, now
from .rule_execution import digest

POLICY = json.loads(Path(__file__).with_suffix('.json').read_text(encoding='utf-8'))


def replacement_id(change):
    return 'policy-cleanup-' + hashlib.sha256((change['kind'] + '\n' + change['text']).encode()).hexdigest()[:24]


def plan(db):
    rows = list(db.scalars(select(Memory).with_for_update()))
    result = []
    for change in POLICY['changes']:
        existing = next((row for row in rows if row.id == replacement_id(change)), None)
        if existing:
            if existing.text != change['text'] or existing.kind != change['kind'] or existing.scope != change['scope']:
                raise ValueError('Правило после очистки изменено пользователем; автоматическая замена остановлена')
            continue  # Never re-enable a rule the owner has subsequently disabled.
        sources = []
        for expected in change['old_hashes']:
            matches = [r for r in rows if r.enabled and r.kind == change['kind'] and r.scope == change['scope'] and digest(r.text) == expected]
            if len(matches) != 1:
                raise ValueError('Исходное правило изменилось или неоднозначно: ' + expected)
            sources.extend(matches)
        result.append((change, sources))
    return result


def apply(db, actor_id, backup_dir=None):
    actor = db.get(User, actor_id)
    if not actor or not actor.enabled or actor.role != 'owner':
        raise ValueError('Изменение правил разрешено только действующему владельцу')
    changes = plan(db)
    if not changes:
        return {'changed': 0, 'message': 'Эта редакция уже применена'}
    folder = Path(backup_dir) if backup_dir else DATA / 'backups'
    folder.mkdir(parents=True, exist_ok=True)
    backup = folder / ('rules-before-cleanup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    old_rows = [row for _, rows in changes for row in rows]
    backup.write_text(json.dumps({'version': POLICY['version'], 'rules': [
        {'id': r.id, 'text': r.text, 'kind': r.kind, 'scope': r.scope, 'enabled': r.enabled,
         'meta': (db.get(Setting, 'memory-meta:' + r.id).value if db.get(Setting, 'memory-meta:' + r.id) else {})}
        for r in old_rows]}, ensure_ascii=False, indent=2), encoding='utf-8')
    backup.chmod(0o600)
    # Verify the recoverable snapshot before changing any rule.
    if len(json.loads(backup.read_text(encoding='utf-8'))['rules']) != len(old_rows):
        raise ValueError('Не удалось проверить копию правил')
    for change, rows in changes:
        for row in rows:
            row.enabled = False
        rid = replacement_id(change)
        db.add(Memory(id=rid, text=change['text'], kind=change['kind'], scope=change['scope'], enabled=True))
        db.add(Setting(key='memory-meta:' + rid, value={'created_at': now(), 'created_by': actor.id,
            'created_by_name': actor.display_name, 'source': POLICY['version'], 'replaces': [r.id for r in rows]}))
    accounts = db.execute(select(Review.wb_account_id, func.count(Draft.id)).join(Draft, Draft.review_id == Review.id).where(
        Review.is_answered == False, Review.status != 'publishing').group_by(Review.wb_account_id)).all()
    jobs = []
    for account, total in accounts:
        job = Job(id='policy-check-' + hashlib.sha256((POLICY['version'] + ':' + account).encode()).hexdigest()[:24],
            kind='draft_recheck', priority=80, max_attempts=1000000,
            result={'account_id': account, 'policy_scan': True, 'policy_version': POLICY['version'],
                    'check_only': True, 'total': total, 'cursor_id': '', 'requested_by': actor.id,
                    'message': 'Проверка сохранённых текстов по обновлённым правилам, без перегенерации'})
        if not db.get(Job, job.id):
            db.add(job); jobs.append(job.id)
    db.add(Activity(user_id=actor.id, username=actor.username, method='PATCH', path='/memory/policy-cleanup', status=200,
        details={'event': 'owner_policy_cleanup', 'version': POLICY['version'], 'archived': len(old_rows),
                 'created': len(changes), 'verification_jobs': jobs}))
    db.commit()
    return {'archived': len(old_rows), 'created': len(changes), 'backup': str(backup), 'verification_jobs': jobs}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--apply', action='store_true'); parser.add_argument('--actor')
    args = parser.parse_args()
    with Session() as db:
        if args.apply:
            print(json.dumps(apply(db, args.actor), ensure_ascii=False), flush=True)
        else:
            changes = plan(db)
            print(json.dumps({'changes': len(changes), 'archive': sum(len(rows) for _, rows in changes),
                'owners': [{'id': u.id, 'name': u.display_name} for u in db.scalars(select(User).where(User.role == 'owner', User.enabled == True))]}, ensure_ascii=False), flush=True)
