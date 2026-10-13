import unittest
from types import SimpleNamespace
from app.reply_policy import analyze_customer_message
from app.safety import review_intent, rule_reply, validate_reply


class PositiveComparisonTests(unittest.TestCase):
    def setUp(self):
        self.product = SimpleNamespace(name='Очиститель Profoam 2000', category='Бытовая химия', brand='KANGAROO', facts=[])
        self.review = SimpleNamespace(text='', rating=5, risk='NORMAL')

    def test_praise_against_other_cleaners_is_not_failed_effect(self):
        self.review.text = 'Супер очиститель, чистит всё, даже то что не очистили все другие, вместе взятые.'
        self.assertEqual(review_intent(self.review.text), 'POSITIVE_EXPERIENCE')
        self.assertNotIn('NO_RESULT', analyze_customer_message(self.review.text, 5)['complaints'])
        answer = rule_reply(self.review, self.product)
        self.assertNotIn('Извините', answer)
        self.assertNotIn('разочаровал', answer)

    def test_own_failure_and_other_surfaces_remain_complaints(self):
        for text in ('Не очищает, другие средства лучше', 'Другие средства не помогли, и этот не помог', 'Не очищает другие поверхности'):
            self.assertIn('NO_RESULT', analyze_customer_message(text, 1)['complaints'])

    def test_blank_positive_reply_names_product_without_invented_properties(self):
        self.product.name, self.product.category = 'Свеча NGK', 'Свечи'
        answers = {rule_reply(self.review, self.product, revision) for revision in range(3)}
        self.assertEqual(len(answers), 3)
        for answer in answers:
            self.assertIn('Свеча NGK', answer)
            self.assertGreaterEqual(len(answer), 120)
            self.assertNotIn('совместим', answer)
            validate_reply(answer)

    def test_blank_five_star_cleaner_omits_usage_instruction(self):
        answer = rule_reply(self.review, self.product, facts=[{'text':'Распылите средство на поверхность. Через 30 секунд протрите тканью.', 'verification_status':'VERIFIED'}])
        self.assertNotIn('30 секунд', answer)
        self.assertNotIn('Распылите', answer)
        self.assertIn('Profoam', answer)
        self.assertNotIn('Извините', answer)

    def test_five_star_praise_omits_usage_but_keeps_concrete_detail(self):
        self.review.text = 'Отмыл старый жир, отличный очиститель'
        facts=[{'text':'Распылите средство. Через 30 секунд протрите тканью.', 'verification_status':'VERIFIED'}]
        answer=rule_reply(self.review,self.product,facts=facts)
        self.assertIn('результат очистки',answer)
        self.assertNotIn('30 секунд',answer)

    def test_five_star_complaint_and_question_keep_helpful_instruction(self):
        facts=[{'text':'Распылите средство. Через 30 секунд протрите тканью.', 'verification_status':'VERIFIED'}]
        for text in ('Не очищает', 'Как применять средство?'):
            self.review.text=text
            self.assertIn('30 секунд',rule_reply(self.review,self.product,facts=facts))


if __name__ == '__main__':
    unittest.main()
