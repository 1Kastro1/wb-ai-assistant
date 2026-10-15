"""Exact VIN/profile matching and uploaded catalogs use disposable databases."""
import asyncio
import csv
import io
import unittest
from unittest.mock import patch,AsyncMock
from sqlalchemy import select
from test_review_workbench import WorkbenchTests
from app.db import Product,Review,StoreListing,FitmentImport,Draft
from app.fitment_catalog import import_cross_catalog,fitment_answer,parse_rows
from app.services import generate_question_draft,propose_publish

VIN='TSMJYBA2S00828853'

class FitmentTests(unittest.TestCase):
    setUp=WorkbenchTests.setUp
    tearDown=WorkbenchTests.tearDown

    def seed(self,text=None):
        with self.factory() as db:
            db.add_all([Product(id='100001',name='Фильтр масляный',brand='NITTO',category='Масляные фильтры'),Product(id='100002',name='Фильтр масляный другой',brand='NITTO',category='Масляные фильтры'),Product(id='100003',name='Чужой фильтр',brand='NITTO',category='Масляные фильтры')]);db.flush()
            db.add_all([Review(id='fit-q',wb_review_id='fit-q',product_id='100001',rating=0,text=text or 'Подойдет на Suzuki? '+VIN,marketplace='wb_question',wb_account_id='owner'),StoreListing(store_id='owner',product_id='100002'),StoreListing(store_id='other',product_id='100003')]);db.commit()

    def row(self,**extra):
        return dict(brand='NITTO',part_number='00123',wb_nm_id='100001',category='Масляные фильтры',vin=VIN,fit='да',**extra)

    def upload(self,rows,store='owner',verified=True,complete=False):
        output=io.StringIO();headers=list(dict.fromkeys(k for row in rows for k in row));writer=csv.DictWriter(output,fieldnames=headers,delimiter=';');writer.writeheader();writer.writerows(rows)
        with self.factory() as db:return import_cross_catalog(db,output.getvalue().encode(),'cross.csv',store,'https://uniqom.ru/original-catalogs',verified,complete)

    def answer(self):
        with self.factory() as db:return fitment_answer(db,db.get(Review,'fit-q'),db.get(Product,'100001'))

    def test_confirmed_fit_is_short_and_has_no_public_vin(self):
        self.seed();self.upload([self.row()]);r=self.answer();self.assertEqual(r['text'],'Вам подойдет данный товар.');self.assertNotIn(VIN,str(r))
        with self.factory() as db:
            with patch('app.services.ollama.reply_variants',new=AsyncMock()) as model:
                result=asyncio.run(generate_question_draft(db,db.get(Review,'fit-q')))
                model.assert_not_called();self.assertEqual(result['text'],r['text']);self.assertTrue(result['quality']['passed'])

    def test_negative_selects_only_our_listed_wb_alternative(self):
        self.seed();r=self.row();r['fit']='нет';alt=self.row();alt.update(part_number='00456',wb_nm_id='100002');foreign=self.row();foreign.update(part_number='00789',wb_nm_id='100003')
        self.upload([r,alt,foreign]);result=self.answer();self.assertEqual(result['status'],'ALTERNATIVE');self.assertIn('100002',result['text']);self.assertNotIn('100003',result['text'])

    def test_missing_catalog_never_means_no_stock_or_fit(self):
        self.seed();r=self.answer();self.assertEqual(r['status'],'NEEDS_CATALOG');self.assertNotIn('нет товара',r['text'])

    def test_incomplete_vehicle_requests_vin(self):
        self.seed('Подойдет на Suzuki SX4?');self.assertEqual(self.answer()['status'],'NEEDS_VIN')

    def test_complete_vehicle_profile_matches_exact_model_and_body(self):
        self.seed('Подойдет на Suzuki SX4 2018 кузов JY?');row=self.row();row.pop('vin');row.update(make='Suzuki',model='SX4',body='JY',year_from='2013',year_to='2021');self.upload([row]);self.assertEqual(self.answer()['status'],'FIT')
        with self.factory() as db:db.get(Review,'fit-q').text='Подойдет на Suzuki SX40 2018 кузов JY?';db.commit()
        self.assertEqual(self.answer()['status'],'NEEDS_VIN')

    def test_vin_cannot_be_replaced_by_a_similar_model_profile(self):
        self.seed('Подойдет Suzuki SX4 2018 кузов JY '+VIN);row=self.row();row.pop('vin');row.update(make='Suzuki',model='SX4',body='JY',year_from='2013',year_to='2021');self.upload([row]);self.assertEqual(self.answer()['status'],'NEEDS_CATALOG')

    def test_unverified_and_foreign_store_imports_are_not_used(self):
        self.seed();self.upload([self.row()],verified=False);self.upload([self.row()],store='other');self.assertEqual(self.answer()['status'],'NEEDS_CATALOG')

    def test_conflicting_catalogs_require_review(self):
        self.seed();r=self.row();r['fit']='нет';self.upload([self.row(),r]);self.assertEqual(self.answer()['status'],'CONFLICT')

    def test_no_stock_requires_confirmed_complete_assortment(self):
        self.seed();r=self.row();r['fit']='нет';self.upload([r]);self.assertEqual(self.answer()['status'],'NEEDS_CATALOG')
        self.upload([r],complete=True);self.assertEqual(self.answer()['status'],'UNAVAILABLE')

    def test_plain_cross_without_vehicle_evidence_does_not_establish_fit(self):
        self.seed();r=self.row();r.pop('vin');r.update(cross_brand='SUZUKI',cross_number='16510-61A31');self.upload([r]);self.assertEqual(self.answer()['status'],'NEEDS_CATALOG')

    def test_branded_cross_resolves_from_exact_vin_catalog_anchor(self):
        self.seed();anchor=self.row();anchor.update(brand='SUZUKI',part_number='16510-61A31',wb_nm_id='')
        cross=self.row();cross.pop('vin');cross.update(cross_brand='SUZUKI',cross_number='1651061A31');self.upload([anchor,cross]);self.assertEqual(self.answer()['status'],'FIT')

    def test_upload_is_idempotent_and_preserves_leading_zeroes(self):
        self.seed();a=self.upload([self.row()]);b=self.upload([self.row()]);self.assertEqual(a['id'],b['id']);self.assertTrue(b['duplicate'])
        with self.factory() as db:self.assertEqual(db.get(FitmentImport,a['id']).rows[0]['part_number'],'00123');self.assertNotIn(VIN,str(db.get(FitmentImport,a['id']).rows))

    def test_wrong_wb_brand_and_nonexistent_id_are_rejected(self):
        self.seed()
        for updates in ({'brand':'OTHER'},{'wb_nm_id':'999999'}):
            row=self.row();row.update(updates)
            with self.assertRaises(ValueError):self.upload([row])

    def test_xlsx_formulas_and_numeric_part_numbers_are_rejected(self):
        from openpyxl import Workbook
        for value in (123,'=1+1'):
            book=Workbook();book.active.append(['brand','part_number']);book.active.append(['NITTO',value]);data=io.BytesIO();book.save(data)
            with self.assertRaises(ValueError):parse_rows(data.getvalue(),'cross.xlsx')

    def test_upload_endpoint_requires_owner_and_catalogs_are_store_scoped(self):
        self.seed();self.upload([self.row()]);self.assertEqual(len(self.client.get('/fitment/catalogs').json()),1)
        self.auth['role']='manager';r=self.client.post('/fitment/catalogs/import',data={'source':'catalog'},files={'file':('cross.csv',b'brand;part_number\nNITTO;ABC\n','text/csv')});self.assertEqual(r.status_code,403)

    def test_unresolved_catalog_answer_cannot_be_published(self):
        self.seed()
        with self.factory() as db:
            result=asyncio.run(generate_question_draft(db,db.get(Review,'fit-q')));self.assertFalse(result['quality']['passed'])
            with self.assertRaisesRegex(ValueError,'подбор'):propose_publish(db,['fit-q'])

    def test_review_history_alone_is_not_evidence_of_a_current_seller_listing(self):
        self.seed()
        with self.factory() as db:
            db.add(Review(id='old',wb_review_id='old',product_id='100003',rating=5,text='',wb_account_id='owner'));db.commit()
        row=self.row();row.update(wb_nm_id='100003');self.upload([row]);self.assertEqual(self.answer()['status'],'NEEDS_CATALOG')

    def test_owner_can_save_a_reviewed_uniqom_result_but_not_an_arbitrary_domain(self):
        self.seed();body={'brand':'NITTO','part_number':'00123','wb_nm_id':'100001','source':'https://uniqom.ru/original-catalogs','fit':True}
        r=self.client.post('/fitment/questions/fit-q/evidence',json=body);self.assertEqual(r.status_code,200,r.text);self.assertEqual(self.answer()['status'],'FIT')
        body['source']='https://uniqom.ru.evil.example/catalog';self.assertEqual(self.client.post('/fitment/questions/fit-q/evidence',json=body).status_code,400)

    def test_unknown_catalog_restrictions_require_manual_review(self):
        self.seed();row=self.row();row['notes']='Только для конкретной комплектации';self.upload([row]);self.assertEqual(self.answer()['status'],'NEEDS_CATALOG')

    def test_template_is_a_downloadable_excel_compatible_file(self):
        response=self.client.get('/fitment/template');self.assertEqual(response.status_code,200);self.assertIn('attachment',response.headers['content-disposition']);self.assertIn('wb_nm_id',response.text)

    def test_real_xlsx_upload_with_russian_headers_is_used_in_an_answer(self):
        from openpyxl import Workbook
        self.seed();book=Workbook();book.active.append(['Бренд','Артикул производителя','Артикул WB','Категория','vin','Подходит']);book.active.append(['NITTO','00123',100001,'Масляные фильтры',VIN,'да']);data=io.BytesIO();book.save(data)
        result=self.client.post('/fitment/catalogs/import',data={'source':'Наш проверенный каталог','verified':'true','complete':'false'},files={'file':('catalog.xlsx',data.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
        self.assertEqual(result.status_code,200,result.text);self.assertEqual(result.json()['rows'],1);self.assertEqual(self.answer()['status'],'FIT')

    def test_disabled_catalog_stops_influencing_answers_and_can_be_reenabled(self):
        self.seed();item=self.upload([self.row()]);r=self.client.post('/fitment/catalogs/'+item['id']+'/enabled',json={'active':False});self.assertEqual(r.status_code,200);self.assertEqual(self.answer()['status'],'NEEDS_CATALOG')
        self.client.post('/fitment/catalogs/'+item['id']+'/enabled',json={'active':True});self.assertEqual(self.answer()['status'],'FIT')

    def test_upgrade_adds_catalog_tables_without_changing_existing_products(self):
        import importlib.util
        from pathlib import Path
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import inspect
        path=Path(__file__).parents[1]/'migrations/versions/0011_fitment_catalogs.py'
        spec=importlib.util.spec_from_file_location('fitment_upgrade_test',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        FitmentImport.__table__.drop(self.engine);StoreListing.__table__.drop(self.engine)
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade();module.upgrade()
            self.assertIn('fitment_imports',inspect(connection).get_table_names());self.assertIn('store_listings',inspect(connection).get_table_names())
        with self.factory() as db:self.assertIsNotNone(db.get(Product,'p'))

if __name__=='__main__':unittest.main()
