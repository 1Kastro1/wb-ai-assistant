"""Owner-authorized activation of all-market review processing and publication."""
import argparse
import json
from sqlalchemy import select, func
from fastapi import BackgroundTasks
from .db import Session, User, Job, Review, StoreProfile, Account, Setting, Activity, setting, now


def plan(db):
    rows=db.execute(select(Job.kind,Job.status,func.count()).where(
        Job.status.in_(('queued','running'))).group_by(Job.kind,Job.status)).all()
    return {'jobs':[{'kind':k,'status':s,'count':n} for k,s,n in rows],
        'stores':[{'id':s.id,'provider':s.provider,'name':s.name} for s in db.scalars(
            select(StoreProfile).join(Account,Account.id==StoreProfile.id))]}


def activate(db,actor_id):
    from .main import ScheduleInput,schedule_set
    from .automation_schedule import normalize_schedule
    from .scheduled_publication import availability_reason
    from .review_regeneration import enqueue_all
    from .rule_retry import consolidate
    actor=db.get(User,actor_id)
    if not actor or actor.role!='owner' or not actor.enabled:
        raise ValueError('Нужен действующий владелец')
    reason=availability_reason(db)
    if reason:raise ValueError(reason)
    before=normalize_schedule(setting(db,'automation_schedule',{}))
    # Enqueue the full refresh first: it replaces overlapping old review batches
    # but preserves text, checkpoints, questions and publication reservations.
    jobs=enqueue_all(db,actor.id)
    consolidated=consolidate(db)
    stores=list(db.scalars(select(StoreProfile).join(Account,Account.id==StoreProfile.id)))
    row=db.scalar(select(Setting).where(Setting.key=='automation_schedule').with_for_update())
    if row:
        state=dict(row.value or {})
        for store in stores:
            for kind in ('reviews','products','publication'):
                state.pop('last_'+kind+':'+store.id,None)
        row.value=state
    db.commit()
    result=schedule_set(ScheduleInput(
        reviews_sync_enabled=True,drafts_enabled=True,drafts_all_history=True,
        questions_sync_enabled=bool(before['enabled'] and before.get('questions_sync_enabled')),
        question_drafts_enabled=bool(before['enabled'] and before.get('question_drafts_enabled')),
        publication_enabled=True,publication_reviews=True,publication_low_ratings=True,
        publication_all_low_ratings=True,
        publication_questions=bool(before['enabled'] and before.get('publication_questions')),
        publication_markets={'wb':True,'ozon':True,'yandex':True}),
        {'user_id':actor.id,'role':actor.role,'username':actor.username},db,BackgroundTasks())
    db.add(Activity(user_id=actor.id,username=actor.username,method='POST',
        path='/settings/full-review-automation',status=200,
        details={'event':'full_review_automation','refresh_jobs':[j.id for j in jobs],
                 'consolidated_checks':consolidated,'ratings':'1-5','at':now()}))
    db.commit()
    return {'refresh':[{'id':j.id,'store':j.result['account_id'],'total':j.result['total']} for j in jobs],
        'consolidated_checks':consolidated,
        'schedule':{k:result.get(k) for k in ('enabled','drafts_all_history','publication_enabled',
            'publication_reviews','publication_low_ratings','publication_all_low_ratings','publication_markets')}}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--activate',action='store_true');parser.add_argument('--actor')
    args=parser.parse_args()
    with Session() as db:
        print(json.dumps(activate(db,args.actor) if args.activate else plan(db),ensure_ascii=True),flush=True)
