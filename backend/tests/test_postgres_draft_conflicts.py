"""Concurrency regression. Only an explicitly named disposable PostgreSQL database is allowed."""
import os
import time
import unittest
from uuid import uuid4
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from app.db import Base, Product, Review, Draft, StoreProfile
from app.services import edit_draft


@unittest.skipUnless(os.environ.get('WB_POSTGRES_TEST')=='1','Dedicated PostgreSQL test database required')
class PostgresDraftConflicts(unittest.TestCase):
    def test_two_simultaneous_edits_cannot_overwrite_the_same_revision(self):
        url=make_url(os.environ['DATABASE_URL'])
        self.assertEqual(url.database,'wb_workbench_tests','Never use a production database')
        self.assertTrue(url.drivername.startswith('postgresql'))
        engine=create_engine(url,connect_args={'options':'-c statement_timeout=5000 -c lock_timeout=5000'})
        Base.metadata.create_all(engine)
        factory=sessionmaker(bind=engine,expire_on_commit=False)
        prefix='test-'+uuid4().hex
        with factory() as db:
            db.add(StoreProfile(id=prefix,name='Test store',provider='wb'))
            db.add(Product(id=prefix,name='Test product'));db.flush()
            db.add(Review(id=prefix,wb_review_id=prefix,wb_account_id=prefix,product_id=prefix,rating=5,text='',status='unanswered'));db.flush()
            db.add(Draft(id=prefix,review_id=prefix,text='Здравствуйте! Спасибо за Вашу оценку.',original='original',revision=1));db.commit()
        barrier=Barrier(3)
        def update(text):
            with factory() as db:
                barrier.wait(timeout=5)
                try:edit_draft(db,prefix,text,1,learn_preferences=False);return 'saved'
                except ValueError as error:db.rollback();return 'conflict' if 'изменился' in str(error) else str(error)
        with factory() as gate,ThreadPoolExecutor(max_workers=2) as workers:
            # Hold the review row so both edits start before either may finish.
            gate.execute(select(Review).where(Review.id==prefix).with_for_update())
            first=workers.submit(update,'Здравствуйте! Спасибо за высокую оценку товара.')
            second=workers.submit(update,'Здравствуйте! Благодарим за Вашу оценку и выбор товара.')
            barrier.wait(timeout=5);time.sleep(.2);gate.commit()
            self.assertEqual(sorted([first.result(timeout=10),second.result(timeout=10)]),['conflict','saved'])
        with factory() as db:self.assertEqual(db.get(Draft,prefix).revision,2)
        engine.dispose()


if __name__=='__main__':unittest.main()
