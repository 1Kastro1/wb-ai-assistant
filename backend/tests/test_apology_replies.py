import unittest
from types import SimpleNamespace

from app.safety import review_intent, rule_reply, safe_fallback_reply, validate_reply, fresh_reply_variants


class ApologyReplyTests(unittest.TestCase):
    def setUp(self):
        self.product = SimpleNamespace(name='Свеча зажигания BP6HS для Citroen', category='свечи', brand='NGK', facts=[])
        self.review = SimpleNamespace(text='Заказал свечи зажигания производитель нжк япония, пришли х/з', rating=3, risk='NORMAL')

    def test_unclear_manufacturer_uses_description_scenario(self):
        self.assertEqual(review_intent(self.review.text), 'DESCRIPTION_MISMATCH')

    def test_ordinary_country_mention_is_not_a_complaint(self):
        self.assertNotEqual(review_intent('Пришли свечи NGK Япония, все понравилось'), 'DESCRIPTION_MISMATCH')

    def test_quick_reply_apologizes_without_return_advice(self):
        for revision in range(3):
            reply = rule_reply(self.review, self.product, revision)
            self.assertIsNotNone(reply)
            self.assertIn('извин', reply.lower())
            self.assertIn('маркиров', reply.lower())
            self.assertIn('в ПВЗ', reply)
            self.assertIn('если обнаружите несоответствие', reply)
            self.assertIn('отказаться от получения товара', reply)
            self.assertNotIn('был понятен', reply.lower())
            self.assertNotIn('должен быть ясно указан', reply.lower())
            self.assertNotIn('возврат', reply.lower())
            self.assertTrue('спасибо' in reply.lower() or 'благодар' in reply.lower())
            self.assertNotIn('учт', reply.lower())
            self.assertNotIn('поддел', reply.lower())
            validate_reply(reply)

    def test_repeated_generation_has_different_safe_variants(self):
        replies = {rule_reply(self.review, self.product, i) for i in range(3)}
        self.assertEqual(len(replies), 3)

    def test_regeneration_excludes_previous_even_if_its_score_would_win(self):
        replies = [rule_reply(self.review, self.product, i) for i in range(3)]
        for previous in replies:
            choices = fresh_reply_variants(replies, previous)
            self.assertNotIn(previous, choices)
            self.assertTrue(choices)

    def test_required_single_safety_reply_is_preserved(self):
        self.assertEqual(fresh_reply_variants(['safe reply'], 'safe reply'), ['safe reply'])

    def test_model_cannot_suggest_return_applications(self):
        for text in ['Подайте заявку на возврат.', 'Можно оформить возврат товара.']:
            with self.assertRaises(ValueError):
                validate_reply(text)

    def test_model_cannot_reintroduce_internal_improvement_promises(self):
        for text in ['Мы внимательно учтём описанный вами недостаток при работе с информацией о товаре.',
                     'Мы учтем ваш отзыв при работе с карточкой товара.',
                     'Ваш отзыв поможет точнее описывать товар.']:
            with self.assertRaises(ValueError):
                validate_reply(text)

    def test_model_cannot_reintroduce_abstract_manufacturer_templates(self):
        for text in ['Понимаем Ваши сомнения: важно, чтобы производитель полученного товара был понятен.',
                     'Производитель должен быть ясно указан на товаре и упаковке.',
                     'Вы рассчитывали получить товар, на котором производитель и маркировка указаны понятно.']:
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
