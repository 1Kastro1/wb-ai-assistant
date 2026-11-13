"""Explicit owner-requested activation with the queue consumer stopped.

Normal application startup never changes consent or user schedules.
"""
import json
import time
from sqlalchemy import select
from .db import Session,Job,setting,set_setting,now
from .automation_schedule import normalize_schedule,enqueue_due_jobs

VERSION='live-review-pipeline-v1'


def consolidate_checks(db):
    """Union pending policy scopes, keeping old cursors/results as history."""
    from .rule_change_recheck import _queue,policy_version
    groups={}
    for job in db.scalars(select(Job).where(Job.kind=='draft_recheck',Job.status=='queued',Job.cancel_requested==False)):
        state=job.result or {}
        if not (state.get('rule_change') and state.get('policy_scan') and state.get('check_only')) or state.get('manual_check') or state.get('policy_regeneration'):
            continue
        groups.setdefault(state.get('account_id') or 'owner',[]).append(job)
    replaced=0
    for account,jobs in groups.items():
        if len(jobs)<2:continue
        scopes={}
        for job in jobs:
            state=job.result or {}
            items=state.get('rule_scopes') or [{'id':'recovery-audience', 'text':'', 'scope':'global',
                'kind':'REVIEW_RULE' if state.get('reviews_only') else 'GLOBAL_RULE'}]
            for item in items:scopes[json.dumps(item,sort_keys=True)]=item
        target=_queue(db,account,policy_version(db),list(scopes.values()),'',VERSION)
        for old in jobs:
            if old.id==target.id:continue
            old.status,old.cancel_requested='cancelled',True
            old.result={**(old.result or {}),'superseded_by':target.id,
                'message':'Проверка продолжится в объединённом проходе по текущим правилам; прежний прогресс сохранён.'}
            replaced+=1
    return replaced


def activate(db):
    if setting(db,VERSION):return {'already_applied':True}
    schedule=normalize_schedule(setting(db,'automation_schedule',{}))
    set_setting(db,VERSION+'-backup',{'schedule':dict(schedule),'at':now()})
    converted=0
    for audit in db.scalars(select(Job).where(Job.kind=='review_library_audit',
            Job.status.in_(('queued','running')),Job.cancel_requested==False)):
        audit.result={**(audit.result or {}),'fair_markets':True}
    for job in db.scalars(select(Job).where(Job.kind=='drafts',Job.status.in_(('queued','running')),
            Job.cancel_requested==False)):
        state=job.result or {}
        if state.get('scheduled') and not state.get('since') and not state.get('selected_ids'):
            job.result={**state,'archive':True,'fresh_reviews':False};converted+=1
    # The owner explicitly requested live new-review processing. Retain chosen
    # intervals, question controls, market switches and publication permission.
    schedule.update(enabled=True,reviews_sync_enabled=True,drafts_enabled=True,sync_enabled=True)
    enqueue_due_jobs(db,schedule,time.time())
    set_setting(db,'automation_schedule',schedule)
    result={'at':now(),'converted_archive_jobs':converted,'consolidated_checks':consolidate_checks(db)}
    set_setting(db,VERSION,result)
    return result


if __name__=='__main__':
    import sys
    with Session() as db:
        if '--apply' not in sys.argv:
            print(json.dumps({'applied':False,'schedule_enabled':bool(setting(db,'automation_schedule',{}).get('enabled'))}))
        else:
            result=activate(db);db.commit();print(json.dumps(result))
