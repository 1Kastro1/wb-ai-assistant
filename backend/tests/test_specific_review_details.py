import unittest
import ast
import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace
from app.review_cases import specific_review_case
from app.product_guidance import product_usage_reply, battery_voltage_answer, usage_excerpt
from app.reply_policy import analyze_customer_message, missing_complaints
from app.safety import rule_reply, validate_reply, review_intent, cleaner_effectiveness_complaint


class SpecificReviewDetailsTests(unittest.TestCase):
    def setUp(self):
        self.product = SimpleNamespace(name='Средство для мытья', category='', brand='', facts=[])
        self.review = SimpleNamespace(text='', rating=5, risk='NORMAL')

    def quality(self, answer):
        app_path = Path(importlib.util.find_spec('app').submodule_search_locations[0])
        tree = ast.parse((app_path / 'services.py').read_text(encoding='utf-8'))
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('assess_reply', '_addresses_review_topic')]
        namespace = dict(specific_review_case=specific_review_case, re=re, WORDS=re.compile(r'[а-яё0-9]{4,}', re.I), analyze_customer_message=analyze_customer_message,
                         missing_complaints=missing_complaints, review_intent=review_intent, usage_excerpt=usage_excerpt,
                         cleaner_effectiveness_complaint=cleaner_effectiveness_complaint, INTENT_LABELS={'GENERAL':'Общий'},
                         MIN_DRAFT_QUALITY=96, QUALITY_EVALUATOR_VERSION='test', now=lambda:'test')
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'<quality>','exec'),namespace)
        return namespace['assess_reply'](self.review,self.product,answer,[])

    def test_mixed_review_apology_passes_quality(self):
        self.review.text = 'Один потёк по дороге, упаковали плохо. Сам гель хороший. Рекомендую!'
        self.assertTrue(self.quality(rule_reply(self.review,self.product))['passed'])

    def test_positive_word_endings_match_but_unnecessary_apology_fails(self):
        self.product.name = 'Антифриз'
        self.review.text = 'Очень всё понравилось'
        answer = rule_reply(self.review,self.product)
        self.assertTrue(self.quality(answer)['passed'])
        self.assertFalse(self.quality(answer + ' Извините за разочарование.')['passed'])

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
        self.assertIn('неприятный запах', answer)
        self.assertIn('состояние крышки', answer)
        self.assertIn('в ПВЗ', answer)
        self.assertIn('отказаться от его получения', answer)
        for forbidden in ('провер', 'поддержк', 'подмен', 'фотограф', 'возврат'):
            self.assertNotIn(forbidden, answer.lower())
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

    def test_five_star_positive_details_without_cleaning_instruction(self):
        self.product.name = 'Очиститель Profoam 1000'
        self.review.text = 'Масло и нагар на двигателе отмывает хорошо'
        answer = rule_reply(self.review, self.product, facts=[{'text':'Нанесите средство. Смойте водой.', 'verification_status':'VERIFIED'}])
        self.assertIn('результат очистки', answer)
        self.assertNotIn('Смойте водой', answer)

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
