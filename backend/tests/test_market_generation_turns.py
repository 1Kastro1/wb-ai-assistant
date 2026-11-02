from datetime import datetime,timezone,timedelta
import pytest
from app.db import Session,Job
from app.queue_policy import next_job


@pytest.mark.parametrize('kind,state',[('drafts',{}),('question_drafts',{}),('draft_recheck',{'policy_regeneration':True})])
def test_waiting_generation_gets_turns_across_stores(client,kind,state):
    current=datetime.now(timezone.utc); old=(current-timedelta(minutes=10)).isoformat()
    with Session() as db:
        for number,store in enumerate(('owner','ozon-shop','yandex-shop')):
            db.add(Job(id='batch'+str(number),kind=kind,status='queued',created_at=old,result={**state,'account_id':store}))
        db.add(Job(id='checker',kind='draft_recheck',status='queued',created_at=old,result={'positive_recheck':True}))
        db.commit()
        for expected in ('batch0','batch1','batch2'):
            job=next_job(db);assert job.id==expected
            job.result={**job.result,'last_served_at':current.isoformat()};db.commit()
        assert next_job(db).id=='checker'


def test_stopped_and_backoff_generation_cannot_be_promoted(client):
    current=datetime.now(timezone.utc);old=(current-timedelta(minutes=10)).isoformat()
    with Session() as db:
        db.add(Job(id='stopped',kind='drafts',status='queued',created_at=old,cancel_requested=True))
        db.add(Job(id='backoff',kind='question_drafts',status='queued',created_at=old,result={'retry_at':current.timestamp()+300}))
        db.add(Job(id='single',kind='single_draft',status='queued',created_at=current.isoformat()))
        db.commit();assert next_job(db).id=='single'


def test_clicks_precede_old_batches_then_background_work_rotates(client):
    current=datetime.now(timezone.utc); old=(current-timedelta(minutes=10)).isoformat()
    earlier=(current-timedelta(minutes=20)).isoformat()
    with Session() as db:
        db.add(Job(id='audit',kind='draft_recheck',created_at=earlier,result={'positive_recheck':True,'background':True}))
        db.add(Job(id='click',kind='single_draft',created_at=old))
        db.add(Job(id='batch',kind='drafts',created_at=old))
        db.commit();assert next_job(db).id=='click'
        db.get(Job,'click').status='completed';db.commit();assert next_job(db).id=='audit'
        db.get(Job,'audit').result={'positive_recheck':True,'background':True,'last_served_at':current.isoformat()}
        db.commit();assert next_job(db).id=='batch'
