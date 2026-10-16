"""Reviewed sources, store boundaries, counters and restart migrations."""
import unittest,importlib.util
from pathlib import Path
from sqlalchemy import select,func
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_review_workbench import WorkbenchTests
from app.db import Product,StoreListing,StoreProfile,Review,Draft,RegistrationRequest,setting,set_setting
from app.reviewed_knowledge import matching_knowledge,attach_reviewed_knowledge,refresh_reviewed_knowledge,knowledge_report
from app.verified_instructions import prefer_manufacturer_instructions
from app.services import analytics,review_filter_counts,_review_query

class ReviewedKnowledgeTests(unittest.TestCase):
    setUp=WorkbenchTests.setUp
    tearDown=WorkbenchTests.tearDown

    def test_pause_is_scoped_and_keeps_manual_batches(self):
        from app.db import Job
        with self.factory() as db:
            db.add_all([Job(id='legacy-auto',kind='drafts',status='queued',result={}),
                Job(id='own-auto',kind='drafts',status='queued',result={'account_id':'owner'}),
                Job(id='foreign-auto',kind='drafts',status='queued',result={'account_id':'other'}),
                Job(id='manual-batch',kind='drafts',status='queued',result={'account_id':'owner','manual_batch':True})]);db.commit()
        response=self.client.post('/auto-replies/pause')
        self.assertEqual(response.status_code,200,response.text)
        with self.factory() as db:
            for key in ('legacy-auto','own-auto'):self.assertEqual(db.get(Job,key).status,'cancelled')
            for key in ('foreign-auto','manual-batch'):self.assertEqual(db.get(Job,key).status,'queued')
        self.assertIsNone(self.client.get('/auto-replies/status').json()['current_job'])

    def test_invalid_legacy_source_does_not_break_coverage(self):
        self.seed_knowledge()
        with self.factory() as db:
            db.get(Product,'ngk').facts=[{'text':'invalid','source':'https://[broken','verification_status':'VERIFIED'}];db.commit()
            self.assertEqual(knowledge_report(db,db.get(StoreProfile,'owner'))['with_instructions'],0)

    def test_exact_feature_cannot_match_a_similar_article_or_another_brand(self):
        product=Product(id='wiper',brand='DOUBLE FORCE',name='Щетка стеклоочистителя',part_number='DFF12',facts=[])
        items=matching_knowledge(product);self.assertEqual(len(items),1);self.assertIn('300 мм',items[0]['text'])
        product.part_number='DFF120';self.assertEqual(matching_knowledge(product),[])
        product.part_number='DFF12';product.brand='AVANTECH';self.assertEqual(matching_knowledge(product),[])
        product.brand='DOUBLE FORCE';product.part_number='DFF12/DFF20';self.assertEqual(matching_knowledge(product),[])

    def test_general_guide_does_not_leak_to_glow_plugs_coils_or_shock_mounts(self):
        for brand,name in [('NGK','Свеча накаливания'),('DENSO','Катушка зажигания'),('KYB','Опора амортизатора'),('KOITO','Лампа светодиодная')]:
            with self.subTest(brand=brand):self.assertEqual(matching_knowledge(Product(brand=brand,name=name,part_number='',facts=[])),[])
        for brand,name in [('NGK','Свеча зажигания BP6HS'),('DENSO','Свеча зажигания K16RU11'),('KYB','Амортизатор передний'),('KOITO','Лампа автомобильная H7 галогенная')]:
            with self.subTest(brand=brand):
                matches=matching_knowledge(Product(brand=brand,name=name,part_number='',facts=[]));self.assertTrue(matches);self.assertTrue(all(x['scope']=='brand_category' for x in matches))

    def seed_knowledge(self):
        with self.factory() as db:
            db.add_all([Product(id='ngk',name='Свеча зажигания BP6HS',brand='NGK',facts=[{'text':'Ручной факт','source':'Карточка товара','verification_status':'VERIFIED'}]),Product(id='foreign-ngk',name='Свеча зажигания BP6HS',brand='NGK'),StoreListing(store_id='owner',product_id='ngk'),StoreListing(store_id='other',product_id='foreign-ngk')]);db.commit()

    def test_bulk_refresh_is_store_scoped_idempotent_and_preserves_manual_facts(self):
        self.seed_knowledge()
        with self.factory() as db:
            result=refresh_reviewed_knowledge(db,db.get(StoreProfile,'owner'));self.assertEqual(result['changed_products'],1);self.assertEqual(result['general_instructions'],1)
            facts=db.get(Product,'ngk').facts;self.assertTrue(any(f['text']=='Ручной факт' for f in facts));self.assertEqual(db.get(Product,'foreign-ngk').facts,[])
            guide=next(f for f in facts if f.get('kind')=='instruction');self.assertEqual(guide['applicability_scope'],'brand_category');self.assertEqual(guide['source_checked_at'],'2026-10-16')
            self.assertEqual(setting(db,'official_domain:ngk'),'ngksparkplugs.com')
            again=refresh_reviewed_knowledge(db,db.get(StoreProfile,'owner'));self.assertEqual(again['added_facts'],0)

    def test_owner_domain_conflict_is_never_overwritten(self):
        self.seed_knowledge()
        with self.factory() as db:
            set_setting(db,'official_domain:ngk','approved.example');db.commit()
            result=refresh_reviewed_knowledge(db,db.get(StoreProfile,'owner'));self.assertEqual(result['changed_products'],0);self.assertEqual(setting(db,'official_domain:ngk'),'approved.example')

    def test_specific_recipe_has_priority_over_a_general_guide(self):
        general={'kind':'instruction','source_type':'manufacturer','text':'Общая инструкция','applicability_scope':'brand_category'}
        exact={'kind':'instruction','source_type':'manufacturer','text':'Инструкция для данного артикула'}
        self.assertEqual(prefer_manufacturer_instructions([general,exact]),[exact])

    def test_card_list_and_fact_routes_do_not_expose_another_store(self):
        self.seed_knowledge()
        listed=self.client.get('/products/page').json();self.assertIn('ngk',{p['id'] for p in listed['items']});self.assertNotIn('foreign-ngk',{p['id'] for p in listed['items']})
        self.assertEqual(self.client.post('/products/foreign-ngk/official-domain',json={'domain':'ngksparkplugs.com'}).status_code,404)
        self.assertEqual(self.client.delete('/products/foreign-ngk/facts/0').status_code,404)
        self.assertEqual(self.client.post('/memory/preview',json={'text':'Пиши кратко','example':'Спасибо','product_id':'foreign-ngk'}).status_code,404)
        self.auth.update(role='manager',permissions=['wb:operate'],store_ids=['owner'])
        self.assertEqual(self.client.post('/products/knowledge/refresh').status_code,403)

    def test_manual_counter_matches_filter_and_excludes_answered_low_ratings(self):
        with self.factory() as db:
            summary=analytics(db,account_id='owner');counts=review_filter_counts(db,account_id='owner')
            actual=db.scalar(select(func.count()).select_from(_review_query({'view':'manual','account_id':'owner'}).subquery()))
            self.assertEqual(summary['manual'],counts['manual']);self.assertEqual(actual,counts['manual']);self.assertEqual(actual,1)
            db.get(Review,'r2').is_answered=True;db.commit();self.assertEqual(analytics(db,account_id='owner')['manual'],0);self.assertEqual(review_filter_counts(db,account_id='owner')['manual'],0)

    def test_registration_migration_accepts_an_existing_complete_schema(self):
        path=Path(__file__).parent.parent/'migrations/versions/0010_registration_requests.py'
        spec=importlib.util.spec_from_file_location('registration_migration',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with self.factory() as db:db.add(RegistrationRequest(id='saved-request',username='new.user',password_hash='unused',receipt_hash='hash'));db.commit()
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade();module.upgrade()
        with self.factory() as db:self.assertEqual(db.get(RegistrationRequest,'saved-request').username,'new.user')
