import unittest
from types import SimpleNamespace
from app.product_guidance import product_usage_reply, battery_voltage_answer
from app.reply_policy import analyze_customer_message, missing_complaints
from app.safety import rule_reply, validate_reply


class SpecificReviewDetailsTests(unittest.TestCase):
    def setUp(self):
        self.product = SimpleNamespace(name='Средство для мытья', category='', brand='', facts=[])
        self.review = SimpleNamespace(text='', rating=5, risk='NORMAL')

    def test_mixed_leakage_keeps_both_praise_and_complaint(self):
        self.review.text = 'Один потёк по дороге, упаковали плохо. Сам гель хороший. Рекомендую!'
        answer = rule_reply(self.review, self.product)
        analysis = analyze_customer_message(self.review.text, 5)
        self.assertEqual(analysis['sentiment'], 'mixed')
        self.assertIn('само средство Вам понравилось', answer)
        self.assertIn('протечку', answer)
        self.assertFalse(missing_complaints(analysis, answer))
        validate_reply(answer)

    def test_no_leak_is_not_a_leak_complaint(self):
        self.assertNotIn('LEAKAGE', analyze_customer_message('Средство хорошее, не протекло', 5)['complaints'])

    def test_leak_and_acid_smell_do_not_claim_tampering(self):
        self.review.rating = 2
        self.review.text = 'Крышка без заводской защиты. Одета криво, часть протекла, запах кислый, липкая (возможно подменили)'
        answer = rule_reply(self.review, self.product)
        self.assertIn('протечку', answer)
        self.assertIn('неприятном запахе', answer)
        self.assertIn('нельзя подтвердить', answer)
        self.assertNotIn('нанесите', answer.lower())
        self.assertFalse(missing_complaints(analyze_customer_message(self.review.text, 2), answer))

    def test_fragrance_dislike_uses_individual_perception(self):
        self.product.name, self.product.brand = 'Ароматизатор CLEAR SQUASH', 'EIKOSHA'
        self.review.rating, self.review.text = 1, 'Невыносимый запах лимона. Поставил в туалете вместо машины'
        answer = rule_reply(self.review, self.product)
        self.assertIn('индивидуально', answer)
        self.assertIn('другую композицию', answer)
        self.assertNotIn('неправильно', answer)

    def test_positive_fitment_is_buyers_experience_not_catalog_guarantee(self):
        self.product.name = 'Масляный фильтр'
        self.review.text = 'Подошёл на ниссан ноут 2 поколения'
        answer = rule_reply(self.review, self.product)
        self.assertIn('по Вашему опыту', answer)
        self.assertNotIn('точно', answer)

    def test_positive_details_and_cleaning_instruction(self):
        self.product.name = 'Очиститель Profoam 1000'
        self.review.text = 'Масло и нагар на двигателе отмывает хорошо'
        answer = rule_reply(self.review, self.product, facts=[{'text':'Нанесите средство. Смойте водой.', 'verification_status':'VERIFIED'}])
        self.assertIn('результат очистки', answer)
        self.assertIn('Смойте водой', answer)

    def test_polish_residue_after_correct_use_acknowledged(self):
        self.product.name = 'Полироль Cosmic'
        self.review.rating, self.review.text = 3, 'Слишком вазелиновое состояние и белые разводы, хотя всё правильно полировала'
        answer = product_usage_reply(self.review, self.product, [{'text':'Нанесите тонкий слой. Через 5–15 минут протрите сухой тканью.', 'verification_status':'VERIFIED'}])
        self.assertIn('белые разводы', answer)
        self.assertIn('Вы уже следовали инструкции', answer)
        self.assertIn('5–15 минут', answer)
        self.assertNotIn('неправильно', answer)

    def test_kit_does_not_merge_component_instructions(self):
        self.product.name = 'Набор Profoam 1000 + Profoam 2000'
        self.review.text = ''
        answer = rule_reply(self.review, self.product, facts=[{'text':'Нанесите одно средство на 30 секунд и другое на 10 минут.', 'verification_status':'VERIFIED'}])
        self.assertNotIn('30 секунд', answer)
        self.assertNotIn('10 минут', answer)

    def test_battery_benchmark_conditional_and_sourced(self):
        self.product.name = 'Аккумулятор автомобильный 45 Ач'
        question = SimpleNamespace(text='Сколько вольт должно быть на клеммах при покупке')
        result = battery_voltage_answer(question, self.product)
        self.assertIn('Если речь', result['answer'])
        self.assertIn('12,6–12,8', result['answer'])
        self.assertIn('Номинальные', result['answer'])
        self.assertIn('yuasa.com', result['source']['source'])
        self.product.name = 'Аккумулятор литиевый 24 В'
        self.assertIsNone(battery_voltage_answer(question, self.product))


if __name__ == '__main__':
    unittest.main()
