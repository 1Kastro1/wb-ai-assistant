"""Explicit, reversible owner-authorized review rule consolidation.

Run with --apply only after a verified database backup. No customer text or
publication state is changed. Existing jobs/cursors are never reset.
"""
import argparse
import hashlib
import json
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL
from sqlalchemy import select, func
from .db import Session, Memory, Job, User, Draft, Review, setting, set_setting, now

KEY = 'review-rule-pack:2026-11-02-v143'
PACK = json.loads(Path(__file__).with_suffix('.json').read_text(encoding='utf-8'))


def digest(text):
    return hashlib.sha256(' '.join(text.split()).encode()).hexdigest()


def migrate(db, *, apply=False):
    recorded = setting(db, KEY, None)
    if recorded:
        return {'already_applied': True, 'rules': len(recorded['new_ids']), 'jobs': recorded['jobs']}
    rows = list(db.scalars(select(Memory).where(Memory.enabled == True, Memory.kind == 'REVIEW_RULE').order_by(Memory.id)))
    if sorted(digest(row.text) for row in rows) != sorted(PACK['expected_hashes']):
        raise ValueError('Review rules changed since audit; no changes applied')
    actor = db.scalar(select(User).where(User.role == 'owner', User.enabled == True).order_by(User.id))
    if not actor:
        raise ValueError('Active owner required')
    before = [dict(id=r.id,text=r.text,kind=r.kind,scope=r.scope,enabled=r.enabled) for r in rows]
    result = {'previous_rules':len(rows), 'rules':len(PACK['rules']), 'applied':apply}
    if not apply:
        return result
    for row in rows:
        row.enabled = False
    new_ids = []
    for rule in PACK['rules']:
        mid = str(uuid5(NAMESPACE_URL, KEY + ':' + rule['scope'] + ':' + digest(rule['text'])))
        if db.get(Memory, mid):
            raise ValueError('Unexpected pre-existing replacement; no partial migration allowed')
        db.add(Memory(id=mid,text=rule['text'],kind='REVIEW_RULE',scope=rule['scope'],enabled=True))
        set_setting(db, 'memory-meta:'+mid, {'created_at':now(),'created_by_name':actor.display_name,
            'reason':KEY,'replaces':[r['id'] for r in before if digest(r['text']) == PACK['expected_hashes'][rule['source_index']]]})
        new_ids.append(mid)
    # Verification only: unchanged saved text, no regeneration, no old verdict
    # copied to the new policy. Publication still checks the current fingerprint.
    accounts = list(db.scalars(select(Review.wb_account_id).join(Draft,Draft.review_id == Review.id).where(
        Review.is_answered == False, Review.status != 'publishing', ~Review.marketplace.like('%_question')).distinct()))
    jobs=[]
    for account in accounts:
        jid=str(uuid5(NAMESPACE_URL,KEY+':recheck:'+account))
        total=db.scalar(select(func.count()).select_from(Draft).join(Review,Review.id == Draft.review_id).where(
            Review.wb_account_id == account,Review.is_answered == False,Review.status != 'publishing',~Review.marketplace.like('%_question'))) or 0
        if not db.get(Job,jid):
            db.add(Job(id=jid,kind='draft_recheck',priority=65,max_attempts=1000000,result={
                'account_id':account,'policy_scan':True,'reviews_only':True,'check_only':True,'total':total,
                'requested_by':actor.id,'source_rule_pack':KEY,'message':'Перепроверка сохранённых текстов по обновлённым правилам'}))
        jobs.append(jid)
    set_setting(db,KEY,{'at':now(),'actor_id':actor.id,'previous':before,'new_ids':new_ids,'jobs':jobs})
    db.flush()
    return {**result,'jobs':jobs}


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    with Session() as db:
        result=migrate(db,apply=args.apply)
        if args.apply: db.commit()
        print(json.dumps(result,ensure_ascii=False),flush=True)
