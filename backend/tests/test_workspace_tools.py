"""Workspace preview, mapping and rule changes use a disposable DB."""
import asyncio,json,unittest
from unittest.mock import patch,AsyncMock
from sqlalchemy import select,func
from test_review_workbench import WorkbenchTests
from app.db import Product,StoreListing,FitmentImport,Setting,Memory,Draft,Activity,Job,Review
from app.workspace_tools import preview_cross_catalog,parse_mapping
from app.fitment_catalog import FIELDS,parse_rows,import_cross_catalog
from app.services import analytics

class WorkspaceToolsTests(unittest.TestCase):
    setUp=WorkbenchTests.setUp
    tearDown=WorkbenchTests.tearDown

    def seed(self):
        with self.factory() as db:
            db.add_all([Product(id='100001',name='Фильтр',brand='NITTO',part_number='00123'),StoreListing(store_id='owner',product_id='100001')]);db.commit()

    def file(self,rows='NITTO;00123;100001;AGH30-0430241',headers='бренд;артикул производителя;артикул wb;frame'):
        return (headers+'\n'+rows+'\n').encode()

    def test_preview_maps_columns_deduplicates_and_never_writes(self):
        self.seed()
        with self.factory() as db:
            before={m.__name__:db.scalar(select(func.count()).select_from(m)) for m in (FitmentImport,Setting,Draft)}
            result=preview_cross_catalog(db,self.file('NITTO;00123;100001;AGH30-0430241\nNITTO;00123;100001;AGH30-0430241'),'test.csv','owner')
            self.assertTrue(result['can_import']);self.assertEqual(result['accepted'],1);self.assertEqual(result['duplicates'],1)
            self.assertEqual(result['mapping']['frame'],'vin');self.assertEqual(result['sample'][0]['part_number'],'00123')
            for model in (FitmentImport,Setting,Draft):self.assertEqual(before[model.__name__],db.scalar(select(func.count()).select_from(model)))

    def test_foreign_store_and_mismatched_brand_block_import(self):
        self.seed()
        with self.factory() as db:
            for store,raw in [('other',self.file()),('owner',self.file('BAD;00123;100001;AGH30-0430241'))]:
                result=preview_cross_catalog(db,raw,'test.csv',store);self.assertFalse(result['can_import']);self.assertEqual(result['errors'][0]['row'],2)
        response=self.client.post('/fitment/catalogs/import',files={'file':('test.csv',self.file('BAD;00123;100001;AGH30-0430241'),'text/csv')},data={'source':'Каталог'})
        self.assertEqual(response.status_code,400,response.text)
        with self.factory() as db:self.assertEqual(db.scalar(select(func.count()).select_from(FitmentImport)),0)

    def test_mapping_respects_ignored_columns_and_preserves_article(self):
        self.seed();mapping={'maker':'brand','code':'part_number','wildberries':'wb_nm_id','unused':''}
        with self.factory() as db:
            raw=self.file('NITTO;00123;100001;=ignored','maker;code;wildberries;unused')
            result=preview_cross_catalog(db,raw,'test.csv','owner',mapping);self.assertTrue(result['can_import'])
            imported=import_cross_catalog(db,raw,'test.csv','owner','Официальный каталог',mapping=mapping)
            self.assertEqual(imported['rows'],1);self.assertEqual(db.get(FitmentImport,imported['id']).rows[0]['part_number'],'00123')
            repeated=import_cross_catalog(db,raw,'test.csv','owner','Официальный каталог',mapping=dict(reversed(list(mapping.items()))))
            self.assertTrue(repeated['duplicate'])

    def test_malformed_empty_formula_duplicate_headers_and_mapping(self):
        for raw in [b'',b'brand;brand\nA;B',self.file('NITTO;=formula;100001;AGH30-0430241')]:
            with self.subTest(raw=raw),self.assertRaises(ValueError):parse_rows(raw,'test.csv')
        for value in ['[]','{"a":"brand","b":"brand"}','{"a":"bad"}']:
            with self.assertRaises(ValueError):parse_mapping(value,FIELDS)

    def test_rule_toggle_is_owner_only_and_records_author(self):
        with self.factory() as db:db.add(Memory(id='rule',text='Не используй слово «уважаемый»',scope='global',kind='GLOBAL_RULE'));db.commit()
        response=self.client.patch('/memory/rule/enabled',json={'enabled':False});self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(response.json()['enabled']);self.assertEqual(response.json()['updated_by_name'],'Камиль')
        with self.factory() as db:
            event=db.scalar(select(Activity).where(Activity.path=='/memory/rule/enabled'));self.assertEqual(event.details['event'],'rule_disabled')
        self.auth.update(role='manager',permissions=['wb:operate'],store_ids=['owner'])
        self.assertEqual(self.client.patch('/memory/rule/enabled',json={'enabled':True}).status_code,403)
        self.assertEqual(self.client.post('/fitment/catalogs/preview',files={'file':('test.csv',self.file(),'text/csv')}).status_code,403)

    def test_rule_preview_has_no_persistent_side_effect_and_hides_vehicle_number(self):
        with patch('app.main.ollama.chat',new=AsyncMock(return_value='Добрый день! Уважаемый покупатель, VIN TSMJYBA2S00828853. Спасибо за отзыв!')):
            response=self.client.post('/memory/preview',json={'text':'Не используй слово «уважаемый»','example':'Хороший товар','rating':5})
        self.assertEqual(response.status_code,200,response.text);self.assertNotIn('TSMJYBA2S00828853',response.json()['text']);self.assertNotIn('уважаемый',response.json()['text'].lower())
        with self.factory() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Memory)),0);self.assertEqual(db.scalar(select(func.count()).select_from(Draft)),1)

    def test_rule_preview_handles_model_failure_and_foreign_product(self):
        with patch('app.main.ollama.chat',new=AsyncMock(side_effect=TimeoutError)):
            response=self.client.post('/memory/preview',json={'text':'Пиши кратко','example':'Отличный товар','rating':5})
        self.assertEqual(response.status_code,200,response.text);self.assertIn('не удалось',response.json()['note'])
        response=self.client.post('/memory/preview',json={'text':'Пиши кратко','example':'Товар','product_id':'p'})
        self.assertEqual(response.status_code,404)

    def test_dashboard_counts_are_store_scoped_and_low_ratings_require_review(self):
        with self.factory() as db:
            db.get(Review,'existing').status='draft_ready'
            db.add_all([Job(id='failed-own',kind='drafts',status='failed',result={'account_id':'owner'}),Job(id='failed-other',kind='drafts',status='failed',result={'account_id':'other'})]);db.commit()
            result=analytics(db,account_id='owner');self.assertEqual(result['ready'],1);self.assertEqual(result['manual'],1);self.assertEqual(result['failed_jobs'],1)
