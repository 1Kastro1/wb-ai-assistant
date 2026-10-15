"""Exact VIN/profile matching and uploaded catalogs use disposable databases."""
import asyncio
import csv
import io
import unittest
from unittest.mock import patch,AsyncMock
from sqlalchemy import select
from test_review_workbench import WorkbenchTests
from app.db import Product,Review,StoreListing,FitmentImport,Draft
from app.fitment_catalog import import_cross_catalog,fitment_answer,parse_rows,oem_numbers
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

    def test_vehicle_and_vin_without_question_words_routes_to_catalog(self):
        from app.services import question_intent
        for text in ('Субару форестер vin: JF1SJ5LA3EG137463', VIN, 'VIN: '+VIN.lower()):
            with self.subTest(text=text):self.assertEqual(question_intent(text),'совместимость')
        self.seed(VIN);self.upload([self.row()])
        with self.factory() as db, patch('app.services.ollama.reply_variants',new=AsyncMock()) as model:
            draft=asyncio.run(generate_question_draft(db,db.get(Review,'fit-q')))
            self.assertEqual(draft['text'],'Вам подойдет данный товар.')
            self.assertEqual(draft['quality']['intent_label'],'совместимость');model.assert_not_called()

    def test_bare_vin_without_catalog_stays_manual_without_general_model_reply(self):
        self.seed(VIN)
        with self.factory() as db, patch('app.services.ollama.reply_variants',new=AsyncMock()) as model:
            draft=asyncio.run(generate_question_draft(db,db.get(Review,'fit-q')))
            self.assertEqual(draft['quality']['fitment']['status'],'NEEDS_CATALOG')
            self.assertFalse(draft['quality']['passed']);model.assert_not_called()

    def test_vin_oem_finds_another_brand_only_from_our_wb_store(self):
        self.seed(VIN)
        with self.factory() as db:
            db.get(Product,'100002').brand='AVANTECH'
            db.get(Product,'100003').brand='AVANTECH';db.commit()
        anchor=self.row();anchor.update(brand='SUZUKI',part_number='16510-82703',wb_nm_id='')
        ours=self.oem_row(brand='AVANTECH',part_number='AF123',wb_nm_id='100002')
        foreign=self.oem_row(brand='AVANTECH',part_number='AF456',wb_nm_id='100003')
        self.upload([anchor,ours,foreign])
        result=self.answer();self.assertEqual(result['status'],'ALTERNATIVE')
        self.assertIn('AVANTECH AF123',result['text']);self.assertIn('100002',result['text']);self.assertNotIn('100003',result['text'])

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

    def oem_row(self,**extra):
        row=self.row();row.pop('vin');row.update(oe_number='16510-82703',oe_brand='SUZUKI');row.update(extra);return row

    def test_explicit_oem_matches_normalized_number_and_short_reply_without_model(self):
        self.seed('Оригинальный номер: 1651082703');self.upload([self.oem_row()])
        with self.factory() as db:
            with patch('app.services.ollama.reply_variants',new=AsyncMock()) as model:
                draft=asyncio.run(generate_question_draft(db,db.get(Review,'fit-q')))
                self.assertEqual(draft['text'],'Вам подойдет данный товар.');model.assert_not_called()

    def test_oem_alternative_uses_our_wb_code_and_skips_zero_stock_and_foreign_store(self):
        self.seed('Есть аналог OEM 16510-82703?')
        self.upload([self.oem_row(wb_nm_id='100002',part_number='4SF-102',stock='0'),self.oem_row(wb_nm_id='100003',part_number='FOREIGN')])
        self.assertEqual(self.answer()['status'],'NEEDS_CATALOG')
        self.upload([self.oem_row(wb_nm_id='100002',part_number='4SF-102',stock='2')])
        answer=self.answer();self.assertEqual(answer['status'],'ALTERNATIVE');self.assertIn('100002',answer['text']);self.assertNotIn('не подходит',answer['text'])

    def test_oem_never_matches_a_prefix_or_arbitrary_wb_id_or_vin(self):
        self.seed('Подойдет вместо 16510-8270?');self.upload([self.oem_row()]);self.assertEqual(self.answer()['status'],'NEEDS_CATALOG')
        self.assertFalse(oem_numbers('Артикул WB 1651082703, 2018 год '+VIN));self.assertFalse(oem_numbers('VIN '+VIN))

    def test_oem_without_verified_evidence_stays_manual_and_publish_is_blocked(self):
        self.seed('OEM 16510-82703');self.upload([self.oem_row()],verified=False)
        with self.factory() as db:
            draft=asyncio.run(generate_question_draft(db,db.get(Review,'fit-q')));self.assertFalse(draft['quality']['passed'])
            with self.assertRaises(ValueError):propose_publish(db,['fit-q'])

    def test_oem_brand_collision_or_multiple_requested_numbers_requires_clarification(self):
        self.seed('OEM 16510-82703');self.upload([self.oem_row(),self.oem_row(oe_brand='TOYOTA',wb_nm_id='100002')]);self.assertEqual(self.answer()['status'],'CONFLICT')
        with self.factory() as db:db.get(Review,'fit-q').text='SUZUKI OEM 16510-82703';db.commit()
        self.assertEqual(self.answer()['status'],'FIT')
        with self.factory() as db:db.get(Review,'fit-q').text='OEM 16510-82703 или OEM 90915-10001';db.commit()
        self.assertEqual(self.answer()['status'],'CONFLICT')

    def test_oem_brand_capitalization_is_not_a_catalog_conflict(self):
        self.seed('OEM 16510-82703');self.upload([self.oem_row(oe_brand='SUZUKI'),self.oem_row(oe_brand='Suzuki')])
        self.assertEqual(self.answer()['status'],'FIT')

    def test_oem_cannot_override_vin_or_catalog_restrictions(self):
        self.seed('OEM 16510-82703 '+VIN);self.upload([self.oem_row()]);self.assertEqual(self.answer()['status'],'NEEDS_CATALOG')
        with self.factory() as db:db.get(Review,'fit-q').text='OEM 16510-82703';db.commit()
        self.upload([self.oem_row(notes='Только при специальной комплектации')])
        with self.factory() as db:
            first=db.scalar(select(FitmentImport).order_by(FitmentImport.created_at));first.active=False;db.commit()
        self.assertEqual(self.answer()['status'],'NEEDS_CATALOG')

    def test_vin_original_oem_anchor_finds_branded_oe_analog(self):
        self.seed();anchor=self.row();anchor.update(brand='SUZUKI',part_number='16510-82703',wb_nm_id='')
        self.upload([anchor,self.oem_row()]);self.assertEqual(self.answer()['status'],'FIT')

    def test_owner_can_confirm_an_oem_question_without_vin(self):
        self.seed('OEM 16510-82703');body={'brand':'NITTO','part_number':'4SF-102','wb_nm_id':'100002','source':'https://uniqom.ru/search?term=16510-82703','fit':True}
        pending=self.client.get('/fitment/questions').json();self.assertEqual(next(q for q in pending if q['id']=='fit-q')['oe_number'],'1651082703')
        response=self.client.post('/fitment/questions/fit-q/evidence',json=body);self.assertEqual(response.status_code,200,response.text);self.assertEqual(self.answer()['status'],'ALTERNATIVE')

    def test_owner_can_save_vin_oem_and_reuse_cross_for_another_vin(self):
        from app.fitment_catalog import vin_key
        self.seed(VIN)
        body={'brand':'NITTO','part_number':'00123','wb_nm_id':'100001','source':'https://uniqom.ru/original-catalogs?identString='+VIN,'fit':True,'oe_brand':'SUZUKI','oe_number':'16510-82703','remember_cross':True}
        first=self.client.post('/fitment/questions/fit-q/evidence',json=body)
        self.assertEqual(first.status_code,200,first.text);self.assertTrue(first.json()['reusable_cross']);self.assertEqual(self.answer()['status'],'FIT')
        self.assertTrue(self.client.post('/fitment/questions/fit-q/evidence',json=body).json()['duplicate'])
        with self.factory() as db:
            self.assertEqual(db.get(Product,'100001').part_number,'00123')
            imports=list(db.scalars(select(FitmentImport)));self.assertEqual(len(imports),1);self.assertEqual(len(imports[0].rows),3);self.assertNotIn(VIN,str(imports[0].rows)+imports[0].source)
            # A new VIN is never assumed to share the same original OEM.
            db.get(Review,'fit-q').text='JS2YA4150B6100001';db.commit()
        self.assertEqual(self.answer()['status'],'NEEDS_CATALOG')
        anchor=self.row();anchor.update(vin='JS2YA4150B6100001',brand='SUZUKI',part_number='16510-82703',wb_nm_id='')
        self.upload([anchor]);self.assertEqual(self.answer()['status'],'FIT')

    def test_reusable_cross_requires_paired_oem_and_cannot_reuse_a_negative(self):
        self.seed(VIN)
        body={'brand':'NITTO','part_number':'00123','wb_nm_id':'100001','source':'https://uniqom.ru/original-catalogs','fit':True,'remember_cross':True}
        self.assertEqual(self.client.post('/fitment/questions/fit-q/evidence',json=body).status_code,400)
        body.update(oe_brand='SUZUKI',oe_number='16510-82703',fit=False)
        self.assertEqual(self.client.post('/fitment/questions/fit-q/evidence',json=body).status_code,400)
        body.update(fit=True,oe_number=VIN)
        self.assertEqual(self.client.post('/fitment/questions/fit-q/evidence',json=body).status_code,400)

    def test_reusable_evidence_cannot_change_brand_article_or_foreign_store(self):
        self.seed(VIN)
        with self.factory() as db:db.get(Product,'100001').part_number='00123';db.commit()
        body={'brand':'NITTO','part_number':'00123','wb_nm_id':'100001','source':'https://uniqom.ru/original-catalogs','fit':True,'oe_brand':'SUZUKI','oe_number':'1651082703','remember_cross':True}
        for updates in ({'brand':'OTHER'},{'part_number':'WRONG'},{'wb_nm_id':'100003'}):
            self.assertEqual(self.client.post('/fitment/questions/fit-q/evidence',json={**body,**updates}).status_code,400)
        self.auth['role']='manager'
        self.assertEqual(self.client.post('/fitment/questions/fit-q/evidence',json=body).status_code,403)

    def test_research_shows_saved_fitment_even_when_last_internet_attempt_failed(self):
        from app.db import set_setting
        from app.internet_fitment import cache_key
        self.seed(VIN);self.upload([self.row()])
        with self.factory() as db:
            set_setting(db,cache_key(db,db.get(Review,'fit-q'),db.get(Product,'100001')),{'result':{'status':'ACCESS_REQUIRED'}});db.commit()
        response=self.client.get('/fitment/questions/fit-q/research');self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['status'],'ACCESS_REQUIRED');self.assertEqual(response.json()['saved_fitment']['status'],'FIT');self.assertTrue(response.json()['can_confirm'])

    def test_frame_routes_to_catalog_without_reasking_for_vin(self):
        from app.services import question_intent
        from app.vehicle_identifiers import vehicle_identifiers
        text='Здравствуйте. На Toyota vellfire AGH30-0000123 подойдут?'
        self.assertEqual(vehicle_identifiers(text),['AGH30-0000123'])
        self.assertEqual(question_intent('AGH30-0000123'),'совместимость')
        self.assertFalse(oem_numbers('AGH30-0000123'))
        self.seed(text);self.assertEqual(self.answer()['status'],'NEEDS_CATALOG')
        self.assertNotIn('Пришлите',self.answer()['text'])
        pending=self.client.get('/fitment/questions').json()
        self.assertEqual(next(q for q in pending if q['id']=='fit-q')['vin'],'AGH30-0000123')

    def test_confirmed_frame_is_exact_private_and_reusable(self):
        from app.safety import mask_vin
        self.seed('Toyota AGH30-0000123')
        body={'brand':'NITTO','part_number':'00123','wb_nm_id':'100001','source':'https://uniqom.ru/original-catalogs?identString=AGH30-0000123','fit':True,'oe_brand':'TOYOTA','oe_number':'04465-28520','remember_cross':True}
        response=self.client.post('/fitment/questions/fit-q/evidence',json=body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.answer()['text'],'Вам подойдет данный товар.')
        with self.factory() as db:
            imports=list(db.scalars(select(FitmentImport)))
            self.assertNotIn('0000123',str([x.rows for x in imports])+str([x.source for x in imports]))
            db.get(Review,'fit-q').text='Toyota agh30 – 0000123';db.commit()
        self.assertEqual(self.answer()['status'],'FIT')
        with self.factory() as db:db.get(Review,'fit-q').text='Toyota AGH30-0000124';db.commit()
        self.assertEqual(self.answer()['status'],'NEEDS_CATALOG')
        self.assertNotIn('0000123',mask_vin('Кузов AGH30-0000123'))

    def test_frame_excel_alias_and_multiple_vehicles(self):
        self.seed('AGH30-0000123')
        row=self.row();row.pop('vin');row['frame']='AGH30-0000123';self.upload([row])
        self.assertEqual(self.answer()['status'],'FIT')
        with self.factory() as db:db.get(Review,'fit-q').text='AGH30-0000123 или '+VIN;db.commit()
        self.assertEqual(self.answer()['status'],'NEEDS_VIN')

    def test_frame_does_not_replace_oem_or_wb_ids(self):
        from app.vehicle_identifiers import vehicle_identifiers
        for text in ('OEM 04465-28520','15208-65F0A','WB 143760622','AGH30','2022 год','OEM AGH30-0000123','AGH30-0000123XYZ'):
            with self.subTest(text=text):self.assertFalse(vehicle_identifiers(text))

if __name__=='__main__':unittest.main()
