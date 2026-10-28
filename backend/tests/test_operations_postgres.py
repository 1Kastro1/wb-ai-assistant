"""Actual PostgreSQL JSON queries; only the disposable CI database is allowed."""
import os
import unittest
from uuid import uuid4
from sqlalchemy import create_engine, select, delete
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from app.db import Base, Product, Setting, Job, StoreProfile, Review, Draft, Action, set_setting
from app.product_search import knowledge_condition
from app.queue_policy import next_job


@unittest.skipUnless(os.environ.get('WB_POSTGRES_TEST') == '1', 'Disposable PostgreSQL required')
class PostgresOperations(unittest.TestCase):
    def test_technical_filter_never_hides_low_ratings_or_content_defects(self):
        from app.services import manual_review_condition, technical_review_condition
        url = make_url(os.environ['DATABASE_URL'])
        self.assertEqual(url.database, 'wb_workbench_tests')
        engine = create_engine(url); Base.metadata.create_all(engine); factory = sessionmaker(engine)
        prefix = 'rule-retry-' + uuid4().hex
        try:
            with factory() as db:
                db.add(Product(id=prefix, name='Test')); db.flush()
                for suffix, rating, issues in [('positive',5,[]),('negative',3,[]),('defect',5,['Неверный ответ'])]:
                    db.add(Review(id=prefix+suffix, wb_review_id=prefix+suffix, product_id=prefix,
                        text='Test', rating=rating, status='manual_review', risk='NORMAL', manual=False))
                    db.flush()
                    db.add(Draft(id=prefix+suffix, review_id=prefix+suffix, text='Reply', original='Reply',
                        model='safety-fallback-1.2', quality={'passed':False,'text_issues':issues,
                        'owner_rules':{'passed':False,'technical_error':True,'violations':[]}}))
                db.commit()
                base = select(Review.id).outerjoin(Draft, Draft.review_id == Review.id).where(Review.id.startswith(prefix))
                self.assertEqual(set(db.scalars(base.where(manual_review_condition()))), {prefix+'negative', prefix+'defect'})
                self.assertEqual(len(list(db.scalars(base.where(technical_review_condition())))), 3)
        finally:
            with factory() as db:
                db.execute(delete(Draft).where(Draft.id.startswith(prefix)))
                db.execute(delete(Review).where(Review.id.startswith(prefix)))
                db.execute(delete(Product).where(Product.id == prefix)); db.commit()
            engine.dispose()

    def test_bulk_manifest_double_click_and_pagination_on_postgresql(self):
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        from app import bulk_publication as bulk
        url = make_url(os.environ['DATABASE_URL'])
        self.assertEqual(url.database, 'wb_workbench_tests')
        self.assertTrue(url.drivername.startswith('postgresql'))
        engine = create_engine(url); Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        prefix, aid, jid = 'bulk-ci-'+uuid4().hex, None, None
        try:
            with factory() as db:
                db.add(StoreProfile(id=prefix, name='Test WB', provider='wb'))
                db.add(Product(id=prefix, name='Test')); db.flush()
                for i in range(105):
                    rid = prefix+str(i).zfill(3)
                    db.add(Review(id=rid, wb_review_id=rid, wb_account_id=prefix, product_id=prefix,
                                  marketplace='wb', rating=5, text='', status='draft_ready'))
                db.flush()
                for i in range(105):
                    rid = prefix+str(i).zfill(3)
                    db.add(Draft(id=rid, review_id=rid, text='Добрый день! Спасибо за высокую оценку!',
                                 original='Добрый день! Спасибо за высокую оценку!', quality={'passed': True}))
                db.commit()
            def click():
                with factory() as db:
                    return bulk.start(db, prefix, 'ci-actor').id
            with ThreadPoolExecutor(max_workers=2) as executor:
                ids = list(executor.map(lambda _: click(), range(2)))
            self.assertEqual(ids[0], ids[1]); aid = ids[0]
            with factory() as db:
                jid = db.get(Action, aid).payload['prepare_job_id']
            with patch.object(bulk, 'Session', factory), patch.object(bulk, 'matching_owner_rules', return_value=[]):
                bulk.prepare_step(jid)
            with factory() as db:
                parent = db.get(Action, aid)
                self.assertEqual(parent.status, 'pending')
                self.assertEqual(parent.payload['total'], 105)
                page = bulk.preview(db, parent, 100)
                self.assertEqual(len(page['items']), 5)
                self.assertEqual(page['items'][0]['review_id'], prefix+'100')
                from app import bulk_exclusions as exclusions
                exclusions.save_chunk(db, parent, [{'review_id': prefix+'100', 'reason': 'check_timeout',
                    'at': '2026-10-27T00:00:00+00:00', 'retrospective': True}])
                db.commit()
                filtered = exclusions.page(db, parent, reason='check_timeout', sort='rating_desc')
                self.assertEqual(filtered['total'], 1)
                self.assertEqual(filtered['items'][0]['id'], prefix+'100')
                # Two independent JSON-flattening subqueries must keep their
                # own kind/parent bindings when retrying recovery errors.
                from app import bulk_recovery as recovery
                manifests = list(db.scalars(select(Action).where(Action.kind == bulk.MANIFEST_KIND,
                    Action.payload['parent_id'].as_string() == aid)))
                for manifest in manifests:
                    manifest.payload = {**manifest.payload, 'items': [item for item in manifest.payload['items'] if item['review_id'] != prefix+'100']}
                db.commit()
                recovery_id = recovery.start(db, parent, 'ci-actor')['id']
                self.assertEqual(recovery.start(db, parent, 'ci-actor')['id'], recovery_id)
                recovery_job = db.get(Job, recovery_id)
                self.assertEqual(recovery_job.result['total'], 1)
                chunk = db.scalar(select(Action).where(Action.kind == recovery.ITEMS, Action.payload['parent_id'].as_string() == recovery_id))
                chunk.payload = {**chunk.payload, 'items': [{**chunk.payload['items'][0], 'reason': 'error'}]}
                recovery_job.status = 'completed'
                db.commit()
                retry = recovery.start(db, parent, 'ci-actor', retry_errors=True)
                self.assertEqual(retry['total'], 1)
        finally:
            with factory() as db:
                recovery_ids = list(db.scalars(select(Job.id).where(Job.kind == 'wb_excluded_recovery', Job.result['account_id'].as_string() == prefix)))
                if recovery_ids:
                    db.execute(delete(Action).where(Action.payload['parent_id'].as_string().in_(recovery_ids)))
                    db.execute(delete(Job).where(Job.id.in_(recovery_ids)))
                if aid:
                    db.execute(delete(Action).where(Action.payload['parent_id'].as_string() == aid))
                    db.execute(delete(Action).where(Action.id == aid))
                if jid:
                    db.execute(delete(Job).where(Job.id == jid))
                db.execute(delete(Draft).where(Draft.id.startswith(prefix)))
                db.execute(delete(Review).where(Review.id.startswith(prefix)))
                db.execute(delete(Product).where(Product.id == prefix))
                db.execute(delete(StoreProfile).where(StoreProfile.id == prefix)); db.commit()
            engine.dispose()

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
