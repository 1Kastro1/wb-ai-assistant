import unittest
import re
from types import SimpleNamespace as NS
from app.product_guidance import positive_review_reply
from app.rule_execution import simple_positive, positive_body

class WarmReviewStyleTests(unittest.TestCase):
    def review(self,text,rating=5,market='wb'):
        return NS(id='warm-example',text=text,rating=rating,risk='NORMAL',marketplace=market)

    def test_quality_example_keeps_its_actual_praise(self):
        p=NS(name='Антижир Kangaroo Home',brand='KANGAROO',category='очиститель')
        for market in ('wb','ozon','yandex'):
            answer=positive_review_reply(self.review('Спасибо,очень довольна,долго его ждала и успела за 126р взять,чудесное качество.',market=market),p)
            self.assertIn('качеств',answer)
            self.assertTrue(any(word in answer for word in ('Желаем','Пусть','Пользуйтесь с удовольствием')))
            self.assertLess(len(answer.split()),85)
            for invented in ('быстрая доставка','выгодной','низкой цене','126','Татьяна','по инструкции'):
                self.assertNotIn(invented,answer)

    def test_short_scent_praise_has_no_invented_scene(self):
        for market in ('wb','ozon','yandex'):
            r=self.review('приятный запах.',market=market)
            self.assertTrue(simple_positive(r))
            variants={positive_body(r,revision) for revision in range(3)}
            self.assertEqual(len(variants),3)
            for answer in variants:
                self.assertTrue('аромат' in answer or 'запах' in answer)
                self.assertEqual(answer.lower().count('спасибо')+answer.lower().count('благодарим'),1)
                for invented in ('салон','комфортн','долго держ','Павел','дефлектор','инструкц'):
                    self.assertNotIn(invented,answer)

    def test_question_complaint_and_low_rating_keep_their_own_routes(self):
        p=NS(name='Ароматизатор',brand='EIKOSHA',category='ароматизатор')
        for text,rating in [('Приятный запах, но почти не пахнет',5),('Приятный запах. Как установить?',5),('приятный запах',2)]:
            r=self.review(text,rating)
            self.assertFalse(simple_positive(r))
            self.assertIsNone(positive_review_reply(r,p))
        for risk in ('HIGH','CAUTION'):
            r=self.review('приятный запах');r.risk=risk
            self.assertFalse(simple_positive(r))

    def test_empty_rating_does_not_acquire_experience(self):
        for revision in range(3):
            answer=positive_body(self.review(''),revision)
            self.assertNotIn('понрав',answer)
            self.assertNotIn('аромат',answer)
            self.assertEqual(len([s for s in re.split(r'[.!?]+',answer) if s.strip()]),2)
            self.assertLess(len(answer.split()),35)
            self.assertTrue('Желаем' in answer or 'Пусть' in answer)
            self.assertEqual(answer.lower().count('спасибо')+answer.lower().count('благодарим'),1)


