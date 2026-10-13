"""Standalone unittest: all writes stay in a disposable in-memory database."""
import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db import Product, Review, Draft
from app.services import _review_query,review_filter_counts


class SimpleReviewFilterTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://')
        for model in (Product,Review,Draft):
            model.__table__.create(self.engine)
        self.db=Session(self.engine)
        self.db.add(Product(id='p',name='Cleaner',brand='Test'))
        for name,rating,text in [('one',1,'Bad'),('two',2,''),('three',3,'Average'),('four',4,'Good'),('five',5,'Great'),('empty',5,''),('spaces',4,' \n\t\r '),('question',0,'How?')]:
            self.db.add(Review(id=name,wb_review_id=name,product_id='p',rating=rating,text=text,marketplace='wb_question' if name=='question' else 'wb',manual=name=='five'))
        self.db.add(Draft(id='d',review_id='four',text='Draft',original='Draft',quality={'passed':False}))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def ids(self,view,**filters):
        return {r.id for r in self.db.scalars(_review_query({'view':view,**filters}))}

    def test_positive_range(self):
        self.assertEqual(self.ids('positive'),{'four','five','empty','spaces'})

    def test_negative_range(self):
        self.assertEqual(self.ids('negative'),{'one','two','three'})

    def test_rating_only_accepts_whitespace_but_not_questions(self):
        self.assertEqual(self.ids('rating_only'),{'two','empty','spaces'})

    def test_manual_retains_flags_and_failed_drafts(self):
        self.assertEqual(self.ids('manual'),{'four','five'})

    def test_all_and_search_keep_scope(self):
        self.assertEqual(len(self.ids('all')),7)
        self.assertEqual(self.ids('positive',q='Great'),{'five'})
        self.assertEqual(self.ids('rating_only',account_id='another-store'),set())

    def test_counts_are_for_full_results_and_store(self):
        self.assertEqual(review_filter_counts(self.db),{'all':7,'positive':4,'negative':3,'rating_only':3,'manual':2})
        self.assertEqual(review_filter_counts(self.db,q='Great')['positive'],1)
        self.assertEqual(review_filter_counts(self.db,account_id='another-store')['all'],0)


if __name__=='__main__':
    unittest.main()
