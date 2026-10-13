import unittest
from types import SimpleNamespace

from app.reply_policy import analyze_customer_message, missing_complaints, official_support_reply
from app.safety import validate_reply, safe_fallback_reply, rule_reply


class CustomerPolicyTests(unittest.TestCase):
    def test_mixed_review_keeps_all_complaints(self):
        analysis = analyze_customer_message('Товар отличный, но коробка помята и не хватает деталей', 5)
        self.assertEqual(analysis['sentiment'], 'mixed')
        self.assertEqual(set(analysis['complaints']), {'PACKAGING_ISSUE', 'MISSING_PARTS'})
        self.assertEqual(set(missing_complaints(analysis, 'Спасибо! Отличная покупка.')), set(analysis['complaints']))

    def test_personal_positive_word_is_not_misread_as_negation(self):
        self.assertEqual(analyze_customer_message('Мне понравилось качество', 5)['sentiment'], 'positive')
        self.assertEqual(analyze_customer_message('Мне не понравилось качество', 2)['sentiment'], 'negative')

    def test_question_retains_requested_information(self):
        text = 'Какое напряжение на клеммах аккумулятора?'
        analysis = analyze_customer_message(text)
        self.assertTrue(analysis['is_question'])
        self.assertEqual(analysis['requested_information'], text)
        self.assertFalse(analysis['cause_confirmed'])

    def test_positive_help_is_not_read_as_a_complaint(self):
        for text in ['Мне помогло, отличное средство', 'Мне подошел, хороший товар']:
            analysis = analyze_customer_message(text, 5)
            self.assertEqual(analysis['complaints'], [])
            self.assertEqual(analysis['sentiment'], 'positive')

    def test_money_question_gets_neutral_support_route(self):
        for text in ['Когда вернут деньги?', 'Возвратите мне деньги', 'Нужна компенсация расходов']:
            self.assertTrue(analyze_customer_message(text)['buyer_asks_about_money'])
        reply = validate_reply(official_support_reply())
        self.assertIn('поддержку маркетплейса', reply)
        self.assertNotIn('деньг', reply)

    def test_payout_conditions_and_rights_denial_are_rejected(self):
        for text in ['Вернём Вам деньги в течение трёх дней.', 'Предлагаем возмещение расходов.',
                     'Зачислим 500 рублей на Вашу карту.', 'У Вас нет права на обращение.']:
            with self.assertRaises(ValueError):
                validate_reply(text)

    def test_seller_contact_is_allowed_and_you_is_capitalized(self):
        reply = validate_reply('Здравствуйте! Если вам нужна дополнительная проверка, обратитесь к продавцу через доступный чат.')
        self.assertIn('Вам', reply)
        self.assertIn('обратитесь к продавцу', reply)

    def test_mixed_fallback_does_not_only_thank_for_high_rating(self):
        review = SimpleNamespace(text='Товар отличный, но коробка помята и не хватает деталей', rating=5, risk='NORMAL')
        product = SimpleNamespace(name='Товар', category='', brand='', facts=[])
        self.assertIsNone(rule_reply(review, product))
        reply = validate_reply(safe_fallback_reply(review, product))
        analysis = analyze_customer_message(review.text, review.rating)
        self.assertEqual(missing_complaints(analysis, reply), [])
        self.assertNotIn('возврат', reply.lower())

    def test_description_fallback_is_not_always_about_manufacturer(self):
        review = SimpleNamespace(text='Характеристики не совпадают', rating=2, risk='NORMAL')
        product = SimpleNamespace(name='Товар', category='', brand='', facts=[])
        reply = rule_reply(review, product)
        self.assertIn('описани', reply)
        self.assertNotIn('производител', reply)

    def test_internal_analysis_cannot_leak_into_public_text(self):
        with self.assertRaises(ValueError):
            validate_reply('internal_analysis: primary_complaint QUALITY_ISSUE')


if __name__ == '__main__':
    unittest.main()
