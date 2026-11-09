"""Exercise scheduling and JSON failure predicates against disposable PostgreSQL."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from app.db import Base, Product, Review, Draft, Job
from app.draft_audit import automatic_check_condition
from app.queue_policy import queue_ordering


@unittest.skipUnless(os.environ.get('WB_POSTGRES_TEST') == '1', 'Disposable PostgreSQL required')
class QueueVerificationPostgres(unittest.TestCase):
    def engine(self):
        url=make_url(os.environ['DATABASE_URL'])
        self.assertEqual(url.database,'wb_workbench_tests')
        self.assertTrue(url.drivername.startswith('postgresql'))
        engine=create_engine(url);Base.metadata.create_all(engine)
        return engine

    def test_only_retryable_or_new_pending_drafts_enter_automatic_checker(self):
        engine=self.engine();prefix='failure-'+uuid4().hex
        try:
            with sessionmaker(engine)() as db:
                db.add(Product(id=prefix,name='Test'));db.flush()
                for suffix,rules in [
                    ('retry',{'checked':False,'passed':False,'technical_error':True,'retryable':True}),
                    ('legacy',{'checked':False,'passed':False,'technical_error':True}),
                    ('pending',{'checked':False,'passed':False}),
                    ('blocked',{'checked':False,'passed':False,'technical_error':False,'retryable':False,'status':'blocked'}),
                    ('passed',{'checked':True,'passed':True})]:
                    rid=prefix+suffix
                    db.add(Review(id=rid,wb_review_id=rid,product_id=prefix,rating=5,text='Test',risk='NORMAL',manual=False));db.flush()
                    db.add(Draft(id=rid,review_id=rid,text='Saved text',original='Saved text',revision=9,
                        quality={'passed':rules['passed'],'owner_rules':rules}))
                db.flush()
                rows=set(db.scalars(select(Draft.id).join(Review).where(Draft.id.startswith(prefix),automatic_check_condition())))
                self.assertEqual(rows,{prefix+'retry',prefix+'legacy',prefix+'pending'})
                self.assertEqual(db.get(Draft,prefix+'blocked').text,'Saved text')
                db.rollback()
        finally:engine.dispose()

    def test_old_imports_share_the_oldest_served_band_with_checks_and_publications(self):
        engine=self.engine();prefix='sync-fair-'+uuid4().hex
        current=datetime.now(timezone.utc);old=(current-timedelta(days=5)).isoformat()
        try:
            with sessionmaker(engine)() as db:
                for kind in ('products','reviews','questions'):
                    db.add(Job(id=prefix+kind,kind=kind,priority=200,created_at=old,
                        result={'last_served_at':old,'checkpoint':{'processed':51100}}))
                recent=(current-timedelta(minutes=6)).isoformat()
                for suffix,kind,flags in [('check','draft_recheck',{'technical_scan':True,'policy_scan':True}),
                    ('send','scheduled_publication',{}),('reconcile','publication_reconcile',{})]:
                    db.add(Job(id=prefix+suffix,kind=kind,priority=45,created_at=recent,
                        result={'last_served_at':recent,**flags}))
                db.flush();served=[]
                for _ in range(3):
                    job=db.scalar(select(Job).where(Job.id.startswith(prefix)).order_by(*queue_ordering()).limit(1))
                    served.append(job.kind)
                    job.result={**job.result,'last_served_at':current.isoformat()};db.flush()
                    self.assertEqual(job.result['checkpoint'],{'processed':51100})
                self.assertEqual(set(served),{'products','reviews','questions'})
                db.rollback()
        finally:engine.dispose()
