"""Reviewed rule revision + explicit all-market regeneration, with rules backup."""
import argparse
import json
from pathlib import Path
from sqlalchemy import select
from .db import Session, Job
from . import owner_policy_cleanup
from .review_regeneration import enqueue_all

POLICY = json.loads(Path(__file__).with_suffix('.json').read_text(encoding='utf-8'))


def apply(db, actor_id, backup_dir=None):
    result = owner_policy_cleanup.apply(db, actor_id, backup_dir, POLICY, enqueue_checks=False)
    jobs = enqueue_all(db, actor_id)
    # This campaign replaces older automatic repairs/check-only scans. Leave
    # user batches, sync, questions and publication jobs untouched.
    for old in db.scalars(select(Job).where(Job.kind == 'draft_recheck', Job.status.in_(('queued','running')))):
        state = old.result or {}
        if state.get('context_guidance_repair') or state.get('policy_scan') and state.get('check_only'):
            old.cancel_requested = True
            if old.status == 'queued': old.status = 'cancelled'
    db.commit()
    return {**result, 'regeneration': [{'id':j.id, 'account_id':j.result['account_id'], 'total':j.result['total']} for j in jobs]}


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');parser.add_argument('--actor')
    args=parser.parse_args()
    with Session() as db:
        result=apply(db,args.actor) if args.apply else {'changes':len(owner_policy_cleanup.plan(db,POLICY))}
        print(json.dumps(result,ensure_ascii=True),flush=True)
