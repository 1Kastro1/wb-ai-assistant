"""Assortment recommendations must use this store and preserve product format."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from test_review_workbench import WorkbenchTests
from app.db import Product, Review, StoreListing
from app.services import generate_question_draft, trusted_product_facts, question_intent
from app.assortment_questions import assortment_question_answer


class AssortmentTests(unittest.TestCase):
    setUp = WorkbenchTests.setUp
    tearDown = WorkbenchTests.tearDown

    def seed(self, text='Как закрепить Eikosha на Kia Optima?'):
        with self.factory() as db:
            db.add_all([
                Product(id='200000', name='Держатель на решетку кондиционера X-17', brand='EIKOSHA', facts=[{'text':'Освобождает торпеду. Не ставьте баночку на торпеду.', 'source':'Карточка товара', 'verification_status':'VERIFIED'}]),
                Product(id='200001', name='Меловой ароматизатор CLEAR SQUASH A-24', brand='EIKOSHA'),
                Product(id='200002', name='Держатель на дефлектор CANTULE', brand='EIKOSHA'),
                Product(id='200003', name='Держатель NONSLIP HOLDER', brand='EIKOSHA', facts=[{'text':'Для горизонтальной поверхности салона', 'source':'Карточка товара', 'verification_status':'VERIFIED'}]),
                Product(id='200004', name='Меловой ароматизатор APPLE A-11', brand='EIKOSHA'),
                Product(id='200005', name='Яблочный ароматизатор APPLE', brand='EIKOSHA'),
                Product(id='200006', name='Набор APPLE + CITRUS', brand='EIKOSHA'),
            ]); db.flush()
            db.add_all([StoreListing(store_id='owner', product_id=pid) for pid in ('200000','200001','200002','200003','200004','200006')])
            db.add(StoreListing(store_id='other', product_id='200005'))
            db.add(Review(id='assort-q', wb_review_id='assort-q', product_id='200001', wb_account_id='owner', marketplace='wb_question', text=text, rating=0)); db.commit()

    def answer(self):
        with self.factory() as db:
            return assortment_question_answer(db, db.get(Review,'assort-q'), db.get(Product,'200001'), trusted_product_facts)

    def test_mount_recommends_our_holder_codes_and_appropriate_placement(self):
        self.seed(); result=self.answer()
        for term in ('Добрый день!','200002','200003','подстаканник','кармане двери','ламелей'):
            self.assertIn(term,result['answer'])
        self.assertNotIn('точно подойдет',result['answer'])
        self.assertNotIn('200000',result['answer'])

    def test_scent_search_uses_single_product_from_same_store(self):
        self.seed('А яблочного запаха нет?'); result=self.answer()
        self.assertEqual(result['product_ids'],['200004'])
        self.assertIn('яблочный',result['answer']); self.assertIn('200004',result['answer'])
        self.assertNotIn('200005',result['answer']); self.assertNotIn('200006',result['answer'])

    def test_can_is_not_presented_as_a_giga_refill(self):
        self.seed('А яблочного запаха нет?')
        with self.factory() as db: db.get(Product,'200001').name='Сменный картридж PINK SHOWER V-90'; db.commit()
        result=self.answer(); self.assertIn('не сменные стержни для GIGA',result['answer'])
        with self.factory() as db: db.get(Review,'assort-q').text='Как закрепить этот картридж?'; db.commit()
        self.assertIsNone(self.answer())

    def test_no_scent_match_never_claims_no_stock(self):
        self.seed('Есть вишневый запах?'); result=self.answer()
        self.assertFalse(result['product_ids']); self.assertNotIn('нет в наличии',result['answer'])
        self.assertIn('В загруженном каталоге',result['answer'])

    def test_negative_preference_and_package_question_are_not_recommendations(self):
        self.seed('Не люблю яблочный, есть другой?'); self.assertIsNone(self.answer())
        with self.factory() as db: db.get(Review,'assort-q').text='Держатель входит в комплект?'; db.commit()
        self.assertIsNone(self.answer())
        self.assertEqual(question_intent('Держатель входит в комплект?'),'комплектность')

    def test_generation_and_repeated_generation_do_not_call_model_or_fall_back(self):
        self.seed('А яблочного запаха нет?')
        with self.factory() as db:db.get(Product,'200001').name='Сменный картридж PINK SHOWER V-90';db.commit()
        with self.factory() as db, patch('app.services.ollama.reply_variants',new=AsyncMock()) as model:
            for _ in range(2):
                draft=asyncio.run(generate_question_draft(db,db.get(Review,'assort-q')))
                self.assertEqual(draft['model'],'catalog-assortment-1.0')
                self.assertTrue(draft['quality']['passed']); self.assertIn('200004',draft['text'])
            model.assert_not_called()


if __name__=='__main__': unittest.main()
