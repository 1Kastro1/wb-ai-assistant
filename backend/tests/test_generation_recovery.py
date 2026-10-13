"""Generation failure checks write exclusively to an in-memory database."""
import unittest
from unittest.mock import AsyncMock,patch
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from app.db import Base,Product,Review,Draft
from app.services import generate_draft,generate_question_draft
from app.safety import validate_reply


class GenerationRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.db=Session(self.engine,expire_on_commit=False)
        self.db.add(Product(id='p',name='Автотовар',brand='OTHER',category='Аксессуары',facts=[]))
        self.review=Review(id='r',wb_review_id='r',product_id='p',rating=2,text='Покупкой разочарован',risk='NORMAL')
        self.db.add(self.review)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    async def test_model_connection_failure_returns_checked_fallback(self):
        with patch('app.services.ollama.reply_variants',new=AsyncMock(side_effect=ConnectionError('offline'))) as model:
            result=await generate_draft(self.db,'r')
        self.assertTrue(result['text'])
        self.assertIn('safety-fallback',result['model'])
        validate_reply(result['text'])
        self.assertEqual(model.await_count,1)

    async def test_previous_draft_remains_in_history_when_fallback_replaces_it(self):
        old='Здравствуйте! Спасибо за отзыв. Приносим извинения за разочарование от покупки. Уточните, пожалуйста, что именно Вас не устроило.'
        self.db.add(Draft(id='d',review_id='r',text=old,original=old,revision=1))
        self.db.commit()
        with patch('app.services.ollama.reply_variants',new=AsyncMock(side_effect=TimeoutError())):
            await generate_draft(self.db,'r','Уточни ответ')
        draft=self.db.scalar(select(Draft).where(Draft.review_id=='r'))
        self.assertEqual(draft.edits[-1]['old'],old)
        self.assertEqual(draft.original,old)

    async def test_question_search_timeout_and_model_failure_still_return_draft(self):
        self.review.marketplace='wb_question'
        self.review.rating=0
        self.review.text='Как применять товар?'
        self.db.commit()
        with patch('app.services.web_research.search_instructions',new=AsyncMock(side_effect=TimeoutError())),patch('app.services.ollama.reply_variants',new=AsyncMock(side_effect=ConnectionError())):
            result=await generate_question_draft(self.db,self.review)
        validate_reply(result['text'])
        self.assertTrue(result['id'])


if __name__=='__main__':
    unittest.main()
