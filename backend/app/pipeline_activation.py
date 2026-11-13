"""Explicit owner-requested activation with the queue consumer stopped.

Normal application startup never changes consent or user schedules.
"""
import json
import time
from sqlalchemy import select
from .db import Session,Job,setting,set_setting,now
from .automation_schedule import normalize_schedule,enqueue_due_jobs

VERSION='live-review-pipeline-v1'


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
    result={'at':now(),'converted_archive_jobs':converted}
    set_setting(db,VERSION,result)
    return result


if __name__=='__main__':
    import sys
    with Session() as db:
        if '--apply' not in sys.argv:
            print(json.dumps({'applied':False,'schedule_enabled':bool(setting(db,'automation_schedule',{}).get('enabled'))}))
        else:
            result=activate(db);db.commit();print(json.dumps(result))
