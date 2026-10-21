"""All six owner examples use the same pipeline on WB, Ozon and Yandex."""
import asyncio
import unittest
from unittest.mock import patch, AsyncMock
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import Base, Product, Review, Draft, Memory, StoreProfile
from app.services import generate_draft, draft_public, propose_publish, confirm_action


CASES = [
    ('Очиститель интерьера Kangaroo Profoam 3000, сменный блок', 'Kangaroo', 'отсутствует\nне очищает, запах неприятный', ['30 секунд', 'неприятным'], ['<br', 'не требует смывания']),
    ('EIKOSHA меловой Spirit Refill SQUASH A-9', 'EIKOSHA', 'жалкая породия на Айкошу. раньше она пахла так, что на улице было слышно. этот ароматизатор пахнет кое-как\nне рекомендую', ['официальным дистрибьютором', 'Меловая основа', 'дефлектор', 'держател'], ['Уточните']),
    ('Очиститель Kangaroo Profoam 2000 4,5л', 'Kangaroo', 'Изготовлено 26.07.21.Срок годности 5 лет\nОтправили просроченное средство', ['год–месяц–день', '21 июля 2026', 'Если'], ['Распылите', 'просрочен', 'годен до']),
    ('Дымовой нейтрализатор Carmate Steam Deo D38RU', 'STEAM DEO', 'Подделка', ['ПВЗ', 'отказаться', 'маркировк'], ['дистрибьютор', 'EIKOSHA']),
    ('DFO014 Фильтр масляный DOUBLE FORCE', 'Double Force', 'вместо двух отправили один', ['количество', 'комплектность', 'ПВЗ', 'отказаться'], ['вернём', 'компенсац']),
    ('Щетка стеклоочистителя гибридная Double Force 400 мм', 'Double Force', 'Научили полосить через пару дней', ['стекло', 'кромку', 'креплении', 'заменить'], ['наждач', 'подогните']),
]


class ReviewScreenshotFollowups(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    def test_all_marketplaces_generate_and_regenerate_without_model(self):
        for provider in ('wb', 'ozon', 'yandex'):
            for index, (name, brand, text, includes, excludes) in enumerate(CASES):
                with self.subTest(provider=provider, index=index), self.factory() as db:
                    key = provider + str(index)
                    db.add(StoreProfile(id=key, name=key, provider=provider))
                    db.add(Memory(id=key, scope='brand:EIKOSHA', text='Мы являемся официальным дистрибьютором EIKOSHA.'))
                    facts = [{'text':'Способ применения: Распылите средство.<br/>Оставьте на 30 секунд и промойте водой.<br/><br/>Меры предосторожности:<br/>Не вдыхайте испарения. 600 мл.', 'source':'Карточка товара', 'verification_status':'VERIFIED'}] if index == 0 else []
                    db.add(Product(id=key, name=name, brand=brand, facts=facts, marketplace=provider))
                    db.flush()
                    db.add(Review(id=key, wb_review_id=key, product_id=key, wb_account_id=key, marketplace=provider, text=text, rating=1, risk='NORMAL'))
                    db.commit()
                    with patch('app.services.ollama.reply_variants', new=AsyncMock(side_effect=ValueError('offline'))) as model, patch('app.services.ollama.chat', new=AsyncMock(side_effect=ValueError('offline'))), patch('app.services.web_research.search_instructions', new=AsyncMock(return_value=[])):
                        for _ in range(2):
                            result = asyncio.run(generate_draft(db, key))
                            for token in includes:
                                self.assertIn(token.lower(), result['text'].lower())
                            for token in excludes:
                                self.assertNotIn(token.lower(), result['text'].lower())
                            self.assertNotIn('<br', result['text'])
                        model.assert_not_called()

    def test_legacy_draft_cleanup_is_visible_and_approval_uses_cleaned_revision(self):
        with self.factory() as db:
            db.add(Product(id='p', name='Товар', brand=''))
            db.flush()
            db.add(Review(id='r', wb_review_id='r', product_id='p', text='Хороший', rating=5))
            db.flush()
            draft = Draft(id='d', review_id='r', text='Добрый день!<br/>Спасибо за отзыв.', original='Добрый день!<br/>Спасибо за отзыв.', revision=1)
            db.add(draft)
            db.commit()
            self.assertNotIn('<br', draft_public(db, draft)['text'])
            action = propose_publish(db, ['r'])
            item = action['payload']['items'][0]
            self.assertEqual(item['text'], 'Добрый день!\nСпасибо за отзыв.')
            self.assertEqual(item['revision'], 2)
            self.assertIn('<br', draft.edits[-1]['old'])
            # The normal optimistic lock still refuses a changed draft.
            draft.text = 'Другой текст'
            db.commit()
            with self.assertRaises(ValueError):
                asyncio.run(confirm_action(db, action['id']))


if __name__ == '__main__':
    unittest.main()
