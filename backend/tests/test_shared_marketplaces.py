"""Shared reply policy across providers; isolated SQLite and mocked catalogs/models."""
import asyncio
import csv
import io
import json
import unittest
from unittest.mock import patch, AsyncMock
from test_review_workbench import WorkbenchTests
from app.db import Product, Review, StoreProfile, StoreListing, Memory, set_setting
from app import services, internet_fitment as internet
from app.owner_reply_rules import matching_owner_rules
from app.shared_marketplaces import equivalent_products, internet_fitment_enabled, resolve_listing, product_reference, shared_counters, POLICY_VERSION
from app.fitment_catalog import import_cross_catalog, fitment_answer
from app.verified_instructions import manufacturer_articles

PROVIDERS = ('wb', 'ozon', 'yandex')
FRAME = 'LN130-0100183'


class SharedMarketplaceTests(unittest.TestCase):
    setUp = WorkbenchTests.setUp
    tearDown = WorkbenchTests.tearDown

    def seed_channels(self, name='Меловой ароматизатор A-43', brand='EIKOSHA', article='A-43', category='Ароматизаторы'):
        with self.factory() as db:
            for provider in PROVIDERS:
                store = 'shop-' + provider
                pid = '900001' if provider == 'wb' else provider + ':' + store + ':900001'
                db.add(StoreProfile(id=store, name=store, provider=provider))
                db.add(Product(id=pid, name=name, brand=brand, part_number=article, category=category, marketplace=provider))
                db.flush()
                if provider == 'wb':
                    db.add(StoreListing(store_id=store, product_id=pid))
                db.add(Review(id='review-' + provider, wb_review_id='review-900001', product_id=pid,
                              wb_account_id=store, marketplace=provider, rating=2, text='Очень слабый запах'))
                db.add(Review(id='question-' + provider, wb_review_id='question-900001', product_id=pid,
                              wb_account_id=store, marketplace=provider + '_question', rating=0, text='Из чего корпус?'))
            db.commit()

    def test_owner_review_cases_use_same_pipeline_and_text_on_all_markets(self):
        self.seed_channels()
        replies = []
        with patch('app.services.ollama.reply_variants', new=AsyncMock()) as model, patch('app.services.web_research.search_instructions', new=AsyncMock(return_value=[])):
            for provider in PROVIDERS:
                with self.factory() as db:
                    reply = asyncio.run(services.generate_draft(db, 'review-' + provider))
                    self.assertIn('тонкий, ненавязчивый', reply['text'])
                    self.assertNotIn('Уточните', reply['text'])
                    self.assertEqual(reply['quality']['policy_version'], POLICY_VERSION)
                    replies.append(reply['text'])
            model.assert_not_called()
        self.assertEqual(len(set(replies)), 1)

    def test_exact_identity_shares_rules_and_only_approved_usage_instructions(self):
        self.seed_channels()
        with self.factory() as db:
            wb = db.get(Product, '900001')
            wb.facts = [
                {'text':'Способ применения: установите в совместимый держатель.', 'kind':'instruction', 'source_type':'manufacturer', 'verification_status':'VERIFIED', 'source':'https://eikosha.test/manual'},
                {'text':'В упаковке две штуки', 'kind':'feature', 'source_type':'manufacturer', 'verification_status':'VERIFIED', 'source':'https://eikosha.test/manual'},
                {'text':'Непроверенный совет', 'kind':'instruction', 'source_type':'manufacturer', 'verification_status':'WEB_UNVERIFIED', 'source':'https://eikosha.test/manual'}]
            set_setting(db, 'official_domain:900001', 'eikosha.test')
            db.add(Memory(id='same-product', scope='product:900001', text='Не спрашивай о том, что уже описано.', enabled=True))
            db.add(Memory(id='brand-rule', scope='BRAND:eikosha', text='Мы являемся официальным дистрибьютором EIKOSHA.', enabled=True))
            db.add(Memory(id='disabled-rule', scope='global', text='Не должно попасть в ответ', enabled=False))
            db.add(Product(id='wrong-article', name='Ароматизатор', brand='EIKOSHA', part_number='A-44'))
            db.add(Product(id='wrong-brand', name='Ароматизатор', brand='OTHER', part_number='A-43'))
            db.add(Product(id='kit', name='Набор ароматизаторов', brand='EIKOSHA', part_number='A-43'))
            db.commit()
            for provider in PROVIDERS:
                p = db.get(Product, db.get(Review, 'review-' + provider).product_id)
                rules = matching_owner_rules(db, p)
                self.assertEqual(len(rules), 2)
                if provider != 'wb':
                    facts = services.trusted_product_facts(db, p)
                    self.assertTrue(any(f.get('shared_from_product') == '900001' for f in facts))
                    self.assertFalse(any('В упаковке две' in f['text'] or 'Непроверенный совет' in f['text'] for f in facts))
            for pid in ('wrong-article', 'wrong-brand', 'kit'):
                self.assertNotIn('Не спрашивай о том, что уже описано.', matching_owner_rules(db, db.get(Product, pid)))

    def test_shared_style_counters_and_examples_do_not_depend_on_first_channel(self):
        self.seed_channels()
        with self.factory() as db:
            old = db.get(Review, 'review-wb')
            old.is_answered = True
            old.existing_answer = 'Добрый день! Благодарим за отзыв.'
            set_setting(db, 'learned_edit_preferences:shop-wb', {'apology_added':2})
            set_setting(db, 'learned_edit_preferences:shop-ozon', {'apology_added':3})
            set_setting(db, 'reply_feedback:shop-wb', {'good':{'rules':2}})
            db.commit()
            for provider in reversed(PROVIDERS):
                style = services.owner_style_profile(db, 'shop-' + provider)
                self.assertEqual(style['preferred_greeting'], 'Добрый день')
                self.assertEqual(style['answered_count'], 1)
                review = db.get(Review, 'review-' + provider)
                examples = services.previous_answer_examples(db, review, db.get(Product, review.product_id))
                self.assertEqual(examples[0]['answer'], old.existing_answer)
            self.assertEqual(shared_counters(db, 'learned_edit_preferences:'), {'apology_added':5})
            self.assertEqual(shared_counters(db, 'reply_feedback:'), {'good':{'rules':2}})

    def test_question_model_receives_same_brand_rules_and_style_on_all_markets(self):
        self.seed_channels('Контейнер', 'TEST', 'BOX-1', 'Контейнеры')
        payloads = []
        async def variants(messages, count):
            payloads.append(json.loads(messages[1]['content']))
            return ['Здравствуйте! Корпус этого контейнера изготовлен из полипропилена, как указано в характеристиках товара.']
        with self.factory() as db:
            db.add(Memory(id='common-question', scope='BRAND:test', text='Отвечай по существу, без лишних уточнений.', enabled=True))
            for provider in PROVIDERS:
                p = db.get(Product, db.get(Review, 'question-' + provider).product_id)
                p.facts = [{'text':'Материал корпуса: полипропилен','source':'Карточка товара','verification_status':'VERIFIED'}]
            db.commit()
        with patch('app.services.ollama.reply_variants', side_effect=variants):
            for provider in PROVIDERS:
                with self.factory() as db:
                    result = asyncio.run(services.generate_question_draft(db, db.get(Review, 'question-' + provider)))
                    self.assertEqual(result['quality']['policy_version'], POLICY_VERSION)
        self.assertEqual(len(payloads), 3)
        for payload in payloads:
            self.assertIn('Отвечай по существу, без лишних уточнений.', payload['store_rules'])
            self.assertEqual(payload['reply_style'], payloads[0]['reply_style'])
            self.assertEqual(payload['verified_facts'], payloads[0]['verified_facts'])

    def test_pitman_frame_reaches_catalog_for_each_provider_and_is_never_guessed(self):
        self.seed_channels('Рулевая сошка Toyota Hilux 4WD правая SP2720', '555', 'SP2720', 'Рулевые сошки')
        with self.factory() as db:
            set_setting(db, 'internet-fitment-enabled:shop-wb', True)
            for provider in PROVIDERS:
                db.get(Review, 'question-' + provider).text = 'На хайлюкс сурф ' + FRAME + ' подойдет?'
            db.commit()
        with patch('app.internet_fitment.Session', self.factory), patch.object(internet.catalog, 'decode', new=AsyncMock()) as decode, patch.object(internet.catalog, 'lookup', new=AsyncMock(return_value={'status':'ACCESS_REQUIRED'})) as lookup, patch('app.services.ollama.reply_variants', new=AsyncMock()) as model:
            for provider in PROVIDERS:
                with self.factory() as db:
                    self.assertTrue(internet_fitment_enabled(db, 'shop-' + provider))
                    result = asyncio.run(services.generate_question_draft(db, db.get(Review, 'question-' + provider)))
                    self.assertEqual(result['quality']['fitment']['internet']['status'], 'ACCESS_REQUIRED')
                    self.assertFalse(result['quality']['passed'])
                    self.assertIn('повторно присылать', result['text'])
                    self.assertNotIn(FRAME, result['text'])
            self.assertEqual(lookup.await_count, 3)
            for call in lookup.await_args_list:
                self.assertEqual(call.args, (FRAME, 'pitman_arm'))
            decode.assert_not_called()
            model.assert_not_called()
        with self.factory() as db:
            set_setting(db, 'internet-fitment-enabled:shared', False)
            db.commit()
            for provider in PROVIDERS:
                self.assertFalse(internet_fitment_enabled(db, 'shop-' + provider))

    def test_catalog_product_id_resolves_only_its_store_and_preserves_channel_reference(self):
        self.seed_channels('Фильтр масляный', 'NITTO', '00123', 'Масляные фильтры')
        for provider in PROVIDERS:
            store = 'shop-' + provider
            with self.factory() as db:
                q = db.get(Review, 'question-' + provider)
                q.text = FRAME
                db.commit()
                output = io.StringIO()
                writer = csv.DictWriter(output, fieldnames=['brand','part_number','product_id','category','vin','fit'], delimiter=';')
                writer.writeheader()
                writer.writerow({'brand':'NITTO','part_number':'00123','product_id':q.product_id,'category':'Масляные фильтры','vin':FRAME,'fit':'да'})
                import_cross_catalog(db, output.getvalue().encode(), 'cross.csv', store, 'https://uniqom.ru/original-catalogs', True)
                result = fitment_answer(db, q, db.get(Product, q.product_id))
                self.assertEqual(result['status'], 'FIT')
                self.assertEqual(result['text'], 'Добрый день! По данному Frame Вам подойдет данный товар.')
                self.assertEqual(resolve_listing(db, store, '900001').id, q.product_id)
                self.assertIsNone(resolve_listing(db, store, 'yandex:wrong:900001'))
                label, url = product_reference(db.get(Product, q.product_id))
                if provider != 'wb': self.assertNotIn('Wildberries', label + url)
                if provider == 'yandex': self.assertEqual(url, '')

    def test_verified_marketplace_manufacturer_article_is_provider_neutral(self):
        for source, extra in [('Карточка WB: характеристики', {}), ('Карточка товара', {'source_type':'marketplace'})]:
            p = Product(id='fact-article', name='Товар', facts=[{'source':source, 'verification_status':'VERIFIED', 'text':'Артикул производителя: SP2720', **extra}])
            self.assertEqual(manufacturer_articles(p), {'SP2720'})

    def test_owner_confirmation_and_common_setting_endpoints_for_each_market(self):
        self.seed_channels('Рулевая сошка SP2720', '555', 'SP2720', 'Рулевые сошки')
        for provider in PROVIDERS:
            with self.factory() as db:
                q = db.get(Review, 'question-' + provider)
                q.text = FRAME
                pid = q.product_id
                set_setting(db, 'active_store:actor', 'shop-' + provider)
                db.commit()
            result = self.client.post('/fitment/questions/question-' + provider + '/evidence', json={
                'brand':'555','part_number':'SP2720','product_id':pid,'fit':True,
                'oe_brand':'TOYOTA','oe_number':'45401-35230','remember_cross':True,
                'source':'https://partsouq.com/en/search/all?q=4540135230'})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['fitment']['status'], 'FIT')
        with self.factory() as db:
            set_setting(db, 'active_store:actor', 'shop-wb')
            db.commit()
        response = self.client.post('/fitment/internet/settings', json={'enabled':True})
        self.assertEqual(response.status_code, 200, response.text)
        for provider in PROVIDERS:
            with self.factory() as db:
                set_setting(db, 'active_store:actor', 'shop-' + provider)
                db.commit()
            response = self.client.get('/fitment/internet/settings')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()['enabled'])

    def test_pitman_catalog_label_is_distinct_from_other_steering_parts(self):
        self.assertEqual(internet.target_part(Product(name='Рулевая сошка правая', category='Рулевое управление')), 'pitman_arm')
        self.assertEqual(internet.target_part(Product(name='Наконечник рулевой тяги левый', category='Рулевое управление')), 'tie_rod_end')
        self.assertIn('arm sub-assy, pitman', internet.PARTS['pitman_arm'][2])
        self.assertNotIn('tie rod end', internet.PARTS['pitman_arm'][2])

    def test_legacy_wb_reference_never_becomes_another_marketplace_sku(self):
        from app.db import FitmentImport
        from app.fitment_catalog import vin_key
        self.seed_channels('Фильтр масляный', 'NITTO', '00123', 'Масляные фильтры')
        for provider in ('ozon', 'yandex'):
            store = 'shop-' + provider
            with self.factory() as db:
                q = db.get(Review, 'question-' + provider)
                q.text = FRAME
                product = db.get(Product, q.product_id)
                # The legacy WB ID collides numerically with this store's real SKU.
                rows = [{'brand':'TOYOTA','part_number':'16510-82703','category':product.category,'fit':True,'vin_hash':vin_key(db,store,FRAME)},
                        {'brand':'NITTO','part_number':'OTHER','wb_nm_id':'900001','oe_number':'16510-82703','oe_brand':'TOYOTA','category':product.category,'fit':True}]
                db.add(FitmentImport(id='legacy-'+provider,store_id=store,source='https://uniqom.ru/original-catalogs',digest='legacy-'+provider,verified=True,complete=False,rows=rows))
                db.commit()
                self.assertEqual(fitment_answer(db,q,product)['status'], 'NEEDS_CATALOG')
                self.assertEqual(internet.own_candidates(db,{'store':store,'category':product.category},{'1651082703'}), [])



