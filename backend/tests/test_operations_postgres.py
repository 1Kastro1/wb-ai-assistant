"""Actual PostgreSQL JSON queries; only the disposable CI database is allowed."""
import os
import unittest
from uuid import uuid4
from sqlalchemy import create_engine, select, delete
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from app.db import Base, Product, Setting, Job, StoreProfile, Review, Draft, set_setting
from app.product_search import knowledge_condition
from app.queue_policy import next_job


@unittest.skipUnless(os.environ.get('WB_POSTGRES_TEST') == '1', 'Disposable PostgreSQL required')
class PostgresOperations(unittest.TestCase):
    def test_manual_queue_counts_match_rows_without_a_cartesian_join(self):
        from app.main import work_queue
        url=make_url(os.environ['DATABASE_URL'])
        self.assertEqual(url.database,'wb_workbench_tests')
        self.assertTrue(url.drivername.startswith('postgresql'))
        engine=create_engine(url);Base.metadata.create_all(engine);factory=sessionmaker(engine)
        prefix='manual-queue-'+uuid4().hex
        try:
            with factory() as db:
                db.add(StoreProfile(id=prefix,name='Test',provider='wb'))
                db.add(Product(id=prefix,name='Test'));db.flush()
                for suffix,rating,market,passed in [('negative',2,'wb',None),('failed',5,'wb',False),('good',5,'wb',True),('question',0,'wb_question',None)]:
                    db.add(Review(id=prefix+suffix,wb_review_id=prefix+suffix,wb_account_id=prefix,product_id=prefix,rating=rating,text='',marketplace=market,manual=False));db.flush()
                    if passed is not None:db.add(Draft(id=prefix+suffix,review_id=prefix+suffix,text='Reply',original='Reply',quality={'passed':passed}))
                db.commit()
                result=work_queue({'role':'owner'},db,store_id=prefix,view='manual')
                self.assertEqual(result['total'],2)
                self.assertEqual({row['id'] for row in result['items']},{prefix+'negative',prefix+'failed'})
        finally:
            with factory() as db:
                db.execute(delete(Draft).where(Draft.id.startswith(prefix)))
                db.execute(delete(Review).where(Review.id.startswith(prefix)))
                db.execute(delete(Product).where(Product.id==prefix))
                db.execute(delete(StoreProfile).where(StoreProfile.id==prefix));db.commit()
            engine.dispose()

    def test_catalog_trust_and_queue_json(self):
        url = make_url(os.environ['DATABASE_URL'])
        self.assertEqual(url.database, 'wb_workbench_tests')
        self.assertTrue(url.drivername.startswith('postgresql'))
        engine = create_engine(url)
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine)
        prefix = 'operations-' + uuid4().hex
        try:
            with factory() as db:
                for suffix, source, conflict in [('a','https://docs.brand.test/instruction',False),('b','https://brand.test.evil/instruction',False),('c','https://brand.test/instruction',True)]:
                    db.add(Product(id=prefix+suffix,name=suffix,facts=[{'kind':'instruction','verification_status':'VERIFIED','source':source,'conflict':conflict}]))
                    set_setting(db,'official_domain:'+prefix+suffix,'brand.test')
                db.add(Job(id=prefix,kind='reviews',result={'retry_at':0}))
                db.commit()
                ids=list(db.scalars(select(Product.id).where(Product.id.startswith(prefix),knowledge_condition(db))))
                self.assertEqual(ids,[prefix+'a'])
                self.assertIsNotNone(next_job(db,('reviews',)))
        finally:
            with factory() as db:
                db.execute(delete(Product).where(Product.id.startswith(prefix)))
                db.execute(delete(Setting).where(Setting.key.startswith('official_domain:'+prefix)))
                db.execute(delete(Job).where(Job.id==prefix));db.commit()
            engine.dispose()
