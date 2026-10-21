"""All-category navigation and exact front/rear matching; no live services or customer identifiers."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from test_review_workbench import WorkbenchTests
from app import internet_fitment as internet
from app.db import Product, Review, Setting, FitmentImport, set_setting
from app.fitment_categories import category_plan, CATALOG_SYSTEMS
from app.fitment_catalog import save_question_evidence, fitment_answer

FRAME='ZRT260-0000001'


class AllCategoryFitmentTests(unittest.TestCase):
    setUp=WorkbenchTests.setUp
    tearDown=WorkbenchTests.tearDown

    def test_all_catalog_systems_and_unknown_categories_keep_a_selection_path(self):
        examples=[
            ('Фильтр масляный','service'),('Поршень двигателя','engine'),
            ('Форсунка топливная','fuel'),('Термостат','cooling'),
            ('Глушитель','exhaust'),('Диск сцепления','transmission'),
            ('Рычаг подвески','suspension'),('Сошка рулевая','steering'),
            ('Колодки тормозные передние','brakes'),('Стартер','electrical'),
            ('Компрессор кондиционера','climate'),('Коврики салона','interior'),
            ('Бампер','body'),('Неизвестная новая категория','equipment')]
        for name,expected in examples:
            with self.subTest(name=name):
                p=Product(name=name,category=name)
                plan=category_plan(p,internet.target_part(p))
                self.assertTrue(plan['all_categories'])
                self.assertEqual(plan['group_id'],expected)
                self.assertEqual(len(plan['systems']),14)
                self.assertEqual(plan['uniqom_mode'],'browser')
        self.assertEqual(len(CATALOG_SYSTEMS),14)

    def test_extended_part_routing_keeps_specific_parts_and_axles_distinct(self):
        examples=[
            ('Колодки тормозные передние ADVICS SN138','brake_pad_front'),
            ('Колодки тормозные задние','brake_pad_rear'),
            ('Подшипник ступицы передний','wheel_bearing'),
            ('Ступица колеса','wheel_hub'),('Опора шаровая','ball_joint'),
            ('Насос водяной','water_pump'),('Термостат','thermostat'),
            ('Катушка зажигания','ignition_coil'),('Датчик кислорода','oxygen_sensor'),
            ('Стартер','starter'),('Генератор','alternator'),
            ('Диск сцепления','clutch_disc'),('Ремень ГРМ','timing_belt'),
            ('Щетка стеклоочистителя','wiper_blade'),('Датчик ABS','abs_sensor')]
        for name,expected in examples:
            with self.subTest(name=name):
                self.assertEqual(internet.target_part(Product(name=name,category='')),expected)

    def test_unknown_category_never_claims_an_external_check_or_compatibility(self):
        with self.factory() as db:
            p=db.get(Product,'p');p.name='Новая деталь';p.category='Любая категория'
            q=db.get(Review,'question');q.text='Подойдёт '+FRAME+'?'
            set_setting(db,'internet-fitment-enabled:shared',True);db.commit()
            with patch.object(internet.catalog,'lookup',new=AsyncMock()) as lookup,patch.object(internet.catalog,'decode',new=AsyncMock()) as decode:
                result=asyncio.run(internet.research_question(db,q,p))
                self.assertEqual(result['status'],'CATALOG_SELECTION')
                self.assertEqual(result['provider'],'uniqom_browser')
                self.assertFalse(result['coverage']['automatic_supported'])
                self.assertNotIn(FRAME,str([s.value for s in db.scalars(select(Setting))]))
                self.assertEqual(fitment_answer(db,q,p)['status'],'NEEDS_CATALOG')
                lookup.assert_not_called();decode.assert_not_called()
            self.assertEqual(list(db.scalars(select(FitmentImport))),[])

    def test_coverage_and_exact_article_are_available_without_enabling_auto_lookup(self):
        with self.factory() as db:
            p=db.get(Product,'p');p.name='Колодки тормозные передние';p.category='Колодки'
            p.brand='ADVICS';p.part_number=''
            p.facts=[{'source':'Карточка WB: характеристики','verification_status':'VERIFIED','text':'Артикул производителя: SN138'}]
            q=db.get(Review,'question');q.text=FRAME
            set_setting(db,'internet-fitment-enabled:shared',False);db.commit()
        response=self.client.get('/fitment/questions/question/research')
        self.assertEqual(response.status_code,200,response.text)
        data=response.json()
        self.assertTrue(data['can_confirm'])
        self.assertEqual(data['product']['part_number'],'SN138')
        self.assertEqual(data['coverage']['group_id'],'brakes')
        settings=self.client.get('/fitment/internet/settings').json()
        self.assertFalse(settings['enabled'])
        self.assertTrue(settings['all_categories'])

    def test_checked_uniqom_chain_resolves_front_pads_without_persisting_vehicle_token(self):
        with self.factory() as db:
            p=db.get(Product,'p');p.name='Колодки тормозные передние ADVICS SN138';p.brand='ADVICS';p.category='Колодки';p.part_number='SN138'
            q=db.get(Review,'question');q.text=FRAME;db.commit()
            result=save_question_evidence(db,q,p,{
                'brand':'ADVICS','part_number':'SN138','product_id':'p','fit':True,
                'oe_brand':'TOYOTA','oe_number':'04465-02220','remember_cross':True,
                'source':'https://uniqom.ru/original-catalogs/unit?catalog=TOYOTA00&ssd=opaque-vehicle-context&vehicleId=0&unitId=123&identString='+FRAME})
            self.assertEqual(result['fitment']['status'],'FIT')
            self.assertEqual(result['fitment']['text'],'Добрый день! По данному Frame Вам подойдет данный товар.')
            saved=list(db.scalars(select(FitmentImport)))
            self.assertNotIn(FRAME,str([(x.source,x.rows) for x in saved]))
            self.assertNotIn('opaque-vehicle-context',str([x.source for x in saved]))
            self.assertIn('unitId=123',saved[0].source)
            q.text='ZRT260-0000002';db.commit()
            self.assertEqual(fitment_answer(db,q,p)['status'],'NEEDS_CATALOG')

    def catalog_documents(self, label='PAD KIT, DISC BRAKE, FRONT', note=''):
        context='catalogId=TOYOTA00&carId=fixture&q='+FRAME
        search='<table><tr><td>1</td><td>TOYOTA</td><td><a href="/en/partcatalog/groups/?'+context+'">PREMIO/ALLION</a></td></tr></table>'
        summary='<table class="part-catalog-vehicle-details"><tr><td>'+FRAME+'</td></tr></table>'
        vehicle=summary+'<a href="/en/partcatalog/groups/?'+context+'&groupId=brakes">Brake system</a>'
        group='<article>'+label+'<a href="/en/partcatalog/diagram/?'+context+'&schemaId=front">Front brakes</a></article>'
        diagram=summary+'<article class="part-catalog-part-row"><strong>'+label+'</strong><a class="part-catalog-part-row__number" data-part-number="0446502220">0446502220</a>'+note+'</article>'
        return [internet.Document(doc).root for doc in (search,vehicle,group,diagram)]

    def test_exact_front_pad_label_does_not_confuse_a_direction_with_a_restriction(self):
        with patch.object(internet.catalog,'read',side_effect=self.catalog_documents()):
            result=asyncio.run(internet.catalog.lookup(FRAME,'brake_pad_front'))
            self.assertEqual(result['status'],'FOUND',result)
            self.assertEqual(result['oem_candidates'][0]['oe_number'],'0446502220')
        with patch.object(internet.catalog,'read',side_effect=self.catalog_documents(note=' Only from 2018')):
            self.assertEqual(asyncio.run(internet.catalog.lookup(FRAME,'brake_pad_front'))['status'],'NEEDS_REVIEW')
        with patch.object(internet.catalog,'read',side_effect=self.catalog_documents(label='PAD KIT, DISC BRAKE, REAR')[:3]):
            self.assertEqual(asyncio.run(internet.catalog.lookup(FRAME,'brake_pad_front'))['status'],'NEEDS_REVIEW')

