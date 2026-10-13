import unittest
from types import SimpleNamespace
from app.safety import rule_reply, safe_fallback_reply, validate_reply


class NoExpiryReplyTests(unittest.TestCase):
    def setUp(self):
        self.product = SimpleNamespace(name='Дымовая шашка нейтрализатор запаха с серебром 40 мл', category='Нейтрализатор запаха', brand='STEAM DEO', facts=[])
        self.review = SimpleNamespace(text='Все сделала по инструкции, банка нагрелась, но дым не пошел', rating=1, risk='NORMAL')

    def test_screenshot_gets_concrete_reply_without_repeating_activation(self):
        for revision in range(3):
            answer = rule_reply(self.review, self.product, revision)
            for detail in ('нагре', 'дым не появился', 'следовали инструкции', 'извин'):
                self.assertIn(detail, answer.lower())
            for forbidden in ('просроч', 'влажност', 'возврат', 'повтор', 'push', 'перевер', 'кондиционер'):
                self.assertNotIn(forbidden, answer.lower())
            validate_reply(answer)

    def test_fallback_uses_same_concrete_handling(self):
        self.assertIn('дым не появился', safe_fallback_reply(self.review, self.product))

    def test_all_expiry_claims_are_rejected_including_denials(self):
        for text in ('Возможно, продукт просрочен.', 'Продукт не может быть просрочен.', 'У товара истёк срок годности.', 'Срок годности закончился.', 'Возможно, срок годности истек.'):
            with self.assertRaises(ValueError):
                validate_reply(text)

    def test_normal_apology_is_allowed(self):
        self.assertTrue(validate_reply('Здравствуйте! Извините, что товар не сработал. Для проверки обратитесь в поддержку маркетплейса.'))

    def test_positive_smoke_review_does_not_trigger_failed_activation(self):
        self.review.text, self.review.rating = 'Все сделала по инструкции, дым пошел, запах исчез', 5
        answer = rule_reply(self.review, self.product)
        self.assertTrue(answer is None or 'дым не появился' not in answer)

    def test_other_product_does_not_inherit_smoke_advice(self):
        self.product.name, self.product.category = 'Тормозные колодки', 'Тормозная система'
        self.review.risk = 'HIGH'
        self.assertIn('специалист', rule_reply(self.review, self.product))

    def test_dim_halogen_reply_apologizes_explains_and_acknowledges_color(self):
        self.product.name, self.product.category, self.product.brand = 'Лампа автомобильная H1 галогенная 12В 55Вт 5000К 2 шт', 'Автолампы', 'AVANTECH MAX'
        self.review.text = 'Не соответствует описанию. Свет тусклый и жёлтый'
        answer = rule_reply(self.review, self.product)
        for detail in ('Извините', 'тусклым и жёлтым', 'напряжени', 'возможных причин', 'маркировк'):
            self.assertIn(detail, answer)
        self.assertNotIn('это нормально', answer)
        self.assertNotIn('По отзыву нельзя', answer)
        validate_reply(answer)

    def test_halogen_explanation_is_not_used_for_led_or_positive_review(self):
        self.product.name = 'Лампа LED H1'
        self.review.text = 'Свет тусклый'
        answer = rule_reply(self.review, self.product)
        self.assertTrue(answer is None or 'галогенная лампа' not in answer)
        self.product.name = 'Лампа галогенная H1'
        self.review.text, self.review.rating = 'Свет не тусклый, всё отлично', 5
        answer = rule_reply(self.review, self.product)
        self.assertTrue(answer is None or 'возможных причин' not in answer)

    def test_cleaner_quality_and_expectations_include_apology_instruction_and_next_step(self):
        self.product.name, self.product.category, self.product.brand = 'Очиститель Profoam 2000', 'Бытовая химия', 'KANGAROO'
        facts = [{'text':'Способ применения: Распылите средство. Через 30 секунд протрите тканью и промойте водой.', 'verification_status':'VERIFIED'}]
        for complaint in ('Плохое качество', 'Не оправдало ожидания'):
            self.review.text = complaint
            answer = rule_reply(self.review, self.product, facts=facts)
            for detail in ('Извините', 'соблюдать способ применения', '30 секунд', 'поверхность и загрязнение'):
                self.assertIn(detail, answer)
            self.assertNotIn('По отзыву нельзя', answer)
            validate_reply(answer)

    def test_already_followed_cleaner_instruction_is_acknowledged(self):
        self.product.name, self.product.category = 'Очиститель Profoam', 'Бытовая химия'
        self.review.text = 'Все сделала по инструкции, нет результата'
        answer = rule_reply(self.review, self.product, facts=[{'text':'Нанесите средство.', 'verification_status':'VERIFIED'}])
        self.assertIn('Вы уже следовали инструкции', answer)
        self.assertNotIn('Рекомендуем соблюдать', answer)

    def test_brief_steam_uses_owner_clarification_without_claiming_success(self):
        self.review.text = 'Даже и минуты не дымила. Никакого эффекта. Деньги на ветер.'
        answer = rule_reply(self.review, self.product)
        for detail in ('Извините', 'интенсивное паровыделение не требуется', 'распределения средства', 'отсутствии эффекта'):
            self.assertIn(detail, answer)
        self.assertNotIn('точно сработало', answer)
        validate_reply(answer)


if __name__ == '__main__':
    unittest.main()
