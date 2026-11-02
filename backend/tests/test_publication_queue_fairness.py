from datetime import datetime, timezone, timedelta
from app.db import Session, Job
from app.queue_policy import next_job


def test_approved_publication_cannot_starve_behind_rechecks(client):
    current=datetime.now(timezone.utc)
    old=(current-timedelta(minutes=5)).isoformat()
    with Session() as db:
        db.add(Job(id='send',kind='wb_publish_send',status='queued',created_at=old,result={'last_served_at':old}))
        db.add(Job(id='check',kind='draft_recheck',status='queued',created_at=old,result={'retry_draft_id':'draft'}))
        db.add(Job(id='positive',kind='draft_recheck',status='queued',created_at=old,result={'positive_recheck':True}))
        db.commit()
        assert next_job(db).id=='send'
        send=db.get(Job,'send');send.result={'last_served_at':current.isoformat()};db.commit()
        assert next_job(db).id=='positive'


def test_cancelled_and_backoff_sends_are_never_promoted(client):
    current=datetime.now(timezone.utc)
    old=(current-timedelta(minutes=5)).isoformat()
    with Session() as db:
        db.add(Job(id='cancelled',kind='wb_publish_send',status='queued',created_at=old,cancel_requested=True))
        db.add(Job(id='backoff',kind='wb_publish_send',status='queued',created_at=old,result={'retry_at':current.timestamp()+300}))
        db.add(Job(id='click',kind='single_draft',status='queued',created_at=current.isoformat()))
        db.commit()
        assert next_job(db).id=='click'
        assert next_job(db,('products',)) is None
