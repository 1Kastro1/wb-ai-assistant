"""Dashboard count regressions on isolated SQLite and an opt-in disposable PostgreSQL DB."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import Base, Product, Review, Draft, Job
from app.services import analytics


class AnalyticsCases:
    def seed(self):
        self.scope='analytics-'+uuid4().hex
        self.rows=[]
        with self.factory() as db:
            for suffix in ('p','other-product'):
                db.add(Product(id=self.scope+suffix,name=suffix,brand='Test'))
            db.flush()
            cases=[
                dict(),dict(passed=False),dict(passed=None),dict(manual=True),
                dict(rating=2),dict(model='safety-fallback-v1'),dict(answered=True,passed=False,manual=True),
                dict(risk='DANGER'),dict(status='manual_review'),dict(draft=False),
                dict(rating=1,age=45,draft=False),dict(product='other-product'),
                dict(marketplace='wb_question',rating=0,passed=False),dict(account='foreign'),
            ]
            for index,changes in enumerate(cases):
                row=dict(rating=5,passed=True,manual=False,risk='NORMAL',status='draft_ready',model='rules-v1',
                         answered=False,draft=True,age=0,product='p',marketplace='wb',account='own')
                row.update(changes);self.rows.append(row)
                rid=self.scope+str(index)
                db.add(Review(id=rid,wb_review_id=rid,wb_account_id=self.scope if row['account']=='own' else self.scope+'foreign',
                    product_id=self.scope+row['product'],rating=row['rating'],text='',marketplace=row['marketplace'],
                    is_answered=row['answered'],manual=row['manual'],risk=row['risk'],status=row['status'],topics=['topic'],
                    created_at=(datetime.now(timezone.utc)-timedelta(days=row['age'])).isoformat()))
                db.flush()
                if row['draft']:
                    db.add(Draft(id=rid,review_id=rid,text='',original='',model=row['model'],
                        quality={} if row['passed'] is None else {'passed':row['passed']}))
            for index,(status,account) in enumerate([('failed',self.scope),('interrupted',self.scope),('failed',self.scope+'foreign'),('completed',self.scope)]):
                db.add(Job(id=self.scope+'job'+str(index),kind='drafts',status=status,result={'account_id':account}))
            db.commit()

    def test_counts_preserve_store_product_period_and_review_filters(self):
        self.seed()
        with self.factory() as db:
            for days,product in [(0,''),(30,''),(0,'p'),(30,'other-product')]:
                with self.subTest(days=days,product=product):
                    rows=[r for r in self.rows if r['account']=='own' and not r['marketplace'].endswith('_question')
                          and (not days or r['age']<days) and (not product or r['product']==product)]
                    result=analytics(db,days=days,product_id=self.scope+product if product else '',account_id=self.scope)
                    manual=lambda r:not r['answered'] and (r['rating']<=3 or r['manual'] or r['risk']!='NORMAL'
                        or r['status']=='manual_review' or (r['draft'] and (r['passed'] is False or r['model'].startswith('safety-fallback'))))
                    ready=lambda r:not r['answered'] and r['draft'] and r['rating']>=4 and not r['manual'] and r['risk']=='NORMAL' and r['status']=='draft_ready' and r['passed'] is True
                    self.assertEqual(result['total'],len(rows))
                    self.assertEqual(result['unanswered'],sum(not r['answered'] for r in rows))
                    self.assertEqual(result['manual'],sum(manual(r) for r in rows))
                    self.assertEqual(result['ready'],sum(ready(r) for r in rows))
                    self.assertEqual(result['drafts'],sum(r['draft'] for r in rows))
                    self.assertEqual(result['published'],sum(r['answered'] for r in rows))
                    self.assertEqual(result['failed_jobs'],2)
                    self.assertEqual(result['topics'],{'topic':len(rows)})

    def test_empty_store_is_a_real_zero(self):
        with self.factory() as db:
            result=analytics(db,account_id='empty-'+uuid4().hex)
            for key in ('total','unanswered','manual','ready','failed_jobs','drafts'):
                self.assertEqual(result[key],0)
            self.assertEqual(result['topics'],{})

    def test_quality_summary_reads_saved_data_on_both_databases(self):
        from app.services import quality_report, QUALITY_EVALUATOR_VERSION
        self.seed()
        with self.factory() as db:
            draft = db.get(Draft, self.scope+'0')
            draft.quality = {'score': 96, 'passed': True, 'evaluator_version': QUALITY_EVALUATOR_VERSION}
            draft.edits = [{'by': 'automation'}]
            db.get(Draft, self.scope+'1').edits = [{'by': 'employee'}]
            db.commit()
            result = quality_report(db, account_id=self.scope)
            self.assertEqual(result['scored'], 1)
            self.assertEqual(result['average_score'], 96)
            self.assertEqual(result['passed'], 1)
            self.assertEqual(result['edited'], 1)
            self.assertEqual(result['automated_changes'], 1)
            self.assertEqual(db.get(Draft, self.scope+'0').quality['score'], 96)


class SQLiteDashboardAnalytics(AnalyticsCases,unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory=sessionmaker(bind=self.engine)
    def tearDown(self):self.engine.dispose()


@unittest.skipUnless(os.environ.get('WB_POSTGRES_TEST')=='1','Disposable PostgreSQL database required')
class PostgresDashboardAnalytics(AnalyticsCases,unittest.TestCase):
    def setUp(self):
        url=make_url(os.environ['DATABASE_URL'])
        self.assertEqual(url.database,'wb_workbench_tests','Never run this against production')
        self.assertTrue(url.drivername.startswith('postgresql'))
        self.engine=create_engine(url,connect_args={'options':'-c statement_timeout=5000 -c lock_timeout=5000'})
        Base.metadata.create_all(self.engine)
        self.factory=sessionmaker(bind=self.engine)
    def tearDown(self):self.engine.dispose()


if __name__=='__main__':unittest.main()
