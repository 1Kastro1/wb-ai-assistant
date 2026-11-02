from datetime import datetime, timezone, timedelta
from app.db import Session, Job
from app.queue_policy import next_job
import pytest


@pytest.mark.parametrize('kind,store', [('wb_publish_send','owner'), ('scheduled_publication','owner'),
    ('scheduled_publication','ozon-store'), ('scheduled_publication','yandex-store')])
def test_approved_publication_cannot_starve_behind_rechecks(client, kind, store):
    current=datetime.now(timezone.utc)
    old=(current-timedelta(minutes=5)).isoformat()
    with Session() as db:
        db.add(Job(id='send',kind=kind,status='queued',created_at=old,result={'last_served_at':old,'account_id':store}))
        db.add(Job(id='check',kind='draft_recheck',status='queued',created_at=old,result={'retry_draft_id':'draft'}))
        db.add(Job(id='positive',kind='draft_recheck',status='queued',created_at=old,result={'positive_recheck':True}))
        db.commit()
        assert next_job(db).id=='send'
        send=db.get(Job,'send');send.result={'last_served_at':current.isoformat()};db.commit()
        assert next_job(db).id=='positive'


@pytest.mark.parametrize('kind', ['wb_publish_send','scheduled_publication'])
def test_cancelled_and_backoff_sends_are_never_promoted(client, kind):
    current=datetime.now(timezone.utc)
    old=(current-timedelta(minutes=5)).isoformat()
    with Session() as db:
        db.add(Job(id='cancelled',kind=kind,status='queued',created_at=old,cancel_requested=True))
        db.add(Job(id='backoff',kind=kind,status='queued',created_at=old,result={'retry_at':current.timestamp()+300}))
        db.add(Job(id='click',kind='single_draft',status='queued',created_at=current.isoformat()))
        db.commit()
        assert next_job(db).id=='click'
        assert next_job(db,('products',)) is None


def test_background_policy_scan_cannot_starve_but_yields_to_direct_click(client):
    current=datetime.now(timezone.utc)
    old=(current-timedelta(minutes=6)).isoformat()
    with Session() as db:
        db.add(Job(id='scan',kind='draft_recheck',status='queued',created_at=old,result={'policy_scan':True}))
        db.add(Job(id='background',kind='drafts',status='queued',created_at=current.isoformat(),result={}))
        db.commit()
        assert next_job(db).id=='scan'
        db.add(Job(id='click',kind='single_draft',status='queued',created_at=current.isoformat()))
        db.commit()
        assert next_job(db).id=='click'

