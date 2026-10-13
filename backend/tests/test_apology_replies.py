import unittest
from types import SimpleNamespace

from app.safety import review_intent, rule_reply, safe_fallback_reply, validate_reply


class ApologyReplyTests(unittest.TestCase):
    def setUp(self):
        self.product = SimpleNamespace(name='Свеча зажигания BP6HS для Citroen', category='свечи', brand='NGK', facts=[])
        self.review = SimpleNamespace(text='Заказал свечи зажигания производитель нжк япония, пришли х/з', rating=3, risk='NORMAL')

    def test_unclear_manufacturer_uses_description_scenario(self):
        self.assertEqual(review_intent(self.review.text), 'DESCRIPTION_MISMATCH')

    def test_ordinary_country_mention_is_not_a_complaint(self):
        self.assertNotEqual(review_intent('Пришли свечи NGK Япония, все понравилось'), 'DESCRIPTION_MISMATCH')

    def test_quick_reply_apologizes_and_suggests_checking_labels(self):
        for revision in range(3):
            reply = rule_reply(self.review, self.product, revision)
            self.assertIsNotNone(reply)
            self.assertIn('извин', reply.lower())
            self.assertIn('маркиров', reply.lower())
            self.assertIn('возврат', reply.lower())
            self.assertNotIn('учт', reply.lower())
            self.assertNotIn('поддел', reply.lower())
            validate_reply(reply)

    def test_repeated_generation_has_different_safe_variants(self):
        replies = {rule_reply(self.review, self.product, i) for i in range(3)}
        self.assertEqual(len(replies), 3)

    def test_model_cannot_reintroduce_internal_improvement_promises(self):
        for text in ['Мы внимательно учтём описанный вами недостаток при работе с информацией о товаре.',
                     'Мы учтем ваш отзыв при работе с карточкой товара.',
                     'Ваш отзыв поможет точнее описывать товар.']:
            with self.assertRaises(ValueError):
                validate_reply(text)

    def test_general_negative_fallbacks_do_not_promise_card_changes(self):
        self.review.text = 'Товар разочаровал'
        for revision in range(3):
            reply = safe_fallback_reply(self.review, self.product, revision)
            self.assertIn('извин', reply.lower())
            self.assertNotIn('учт', reply.lower())
            self.assertNotIn('точнее описывать', reply.lower())
            validate_reply(reply)

    def test_safety_critical_reply_keeps_priority_over_description(self):
        self.review.risk = 'HIGH'
        self.assertIn('специалист', rule_reply(self.review, self.product))


if __name__ == '__main__':
    unittest.main()
