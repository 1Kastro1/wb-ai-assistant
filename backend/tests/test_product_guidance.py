import unittest
import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from app.product_guidance import usage_excerpt
from app.safety import rule_reply, safe_fallback_reply, validate_reply, review_intent


class ProductGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.product = SimpleNamespace(name='Очиститель интерьера Profoam 4000+, 600мл', category='Бытовая химия', brand='KANGAROO', facts=[])
        self.review = SimpleNamespace(text='Деньги на ветер', rating=2, risk='NORMAL')
        self.facts = [{'text': 'Способ применения:\nРаспылите средство на место, требующее очистки.\nСильно загрязненные участки потрите щёткой.\nЧерез 30 секунд протрите обработанный участок влажной ветошью или тканевой салфеткой или промойте водой.', 'verification_status': 'VERIFIED'}]

    def test_screenshot_complaint_gets_actual_steps_without_model(self):
        self.assertEqual(review_intent(self.review.text), 'NO_RESULT')
        answer = rule_reply(self.review, self.product, facts=self.facts)
        for step in ('Распылите', 'щёткой', '30 секунд', 'салфеткой', 'или промойте'):
            self.assertIn(step, answer)
        self.assertNotIn('неправильно', answer)
        self.assertNotIn('возврат', answer)
        validate_reply(answer)

    def test_other_cleaner_keeps_its_own_time_and_no_rinsing(self):
        facts = [{'text': 'Способ применения: Нанесите пену. Через 5 минут удалите сухой тканью. Не смывайте водой.', 'verification_status': 'VERIFIED'}]
        answer = safe_fallback_reply(self.review, self.product, facts=facts)
        self.assertIn('5 минут', answer)
        self.assertIn('Не смывайте водой', answer)
        self.assertNotIn('30 секунд', answer)
        self.assertNotIn('щёткой', answer)

    def test_unverified_instruction_cannot_reach_reply(self):
        facts = [{'text': 'Распылите и оставьте на 99 минут.', 'verification_status': 'WEB_UNVERIFIED'}]
        answer = rule_reply(self.review, self.product, facts=facts)
        self.assertNotIn('99', answer)
        self.assertIn('упаковке', answer)
        self.assertIn('поверхность', answer)

    def test_mixed_effectiveness_and_odor_addresses_both(self):
        self.review.text = 'Не очищает и воняет'
        answer = rule_reply(self.review, self.product, facts=self.facts)
        self.assertIn('запах', answer)
        self.assertIn('30 секунд', answer)

    def test_eikosha_weak_scent_handles_vent_holder_and_loose_can(self):
        self.product.name, self.product.brand = 'Air Spencer банка A9', 'EIKOSHA'
        for complaint in ('Слабый запах', 'Почти не пахнет', 'Запах не чувствуется'):
            self.review.text = complaint
            answer = rule_reply(self.review, self.product, facts=[{'kind':'vent_holder','verification_status':'VERIFIED','wb_nm_id':'58434122'}])
            self.assertIn('58434122', answer)
            self.assertIn('дефлекторе', answer)
            self.assertIn('держател', answer)
            self.assertIn('баночку', answer)
            self.assertNotIn('мягким', answer)
            validate_reply(answer)

    def test_missing_holder_id_does_not_invent_accessory(self):
        self.product.name, self.product.brand = 'Air Spencer банка A9', 'EIKOSHA'
        self.review.text = 'Слабый запах'
        answer = rule_reply(self.review, self.product, facts=[])
        self.assertIn('подстаканнике', answer)
        self.assertNotIn('держател', answer)
        self.assertNotIn('артикул', answer)

    def test_disliked_fragrance_offers_individual_choice(self):
        self.product.name, self.product.brand = 'Ароматизатор EIKOSHA', 'EIKOSHA'
        self.review.text = 'Запах не понравился'
        answer = rule_reply(self.review, self.product)
        self.assertIn('индивидуально', answer)
        self.assertIn('другую композицию', answer)

    def test_other_fragrance_and_other_product_instructions(self):
        self.product.name, self.product.brand = 'Ароматизатор другой марки', 'OTHER'
        self.review.text = 'Не пахнет'
        self.assertIn('потоку воздуха', rule_reply(self.review, self.product))
        self.product.name, self.product.category = 'Салфетка для стекла', 'Аксессуары'
        self.review.text = 'Нет результата'
        facts = [{'text': 'Смочите салфетку водой. Протрите стекло.', 'verification_status': 'VERIFIED'}]
        self.assertIn('Протрите стекло', rule_reply(self.review, self.product, facts=facts))

    def test_money_safety_and_damage_keep_priority(self):
        self.review.text = 'Верните деньги, не очищает'
        self.assertNotIn('Распылите', rule_reply(self.review, self.product, facts=self.facts))
        self.review.text, self.review.risk = 'Не тормозит', 'HIGH'
        self.assertIn('специалист', rule_reply(self.review, self.product, facts=self.facts))
        self.review.text, self.review.risk = 'Товар поврежден', 'NORMAL'
        self.assertNotIn('Распылите', rule_reply(self.review, self.product, facts=self.facts))

    def test_usage_excerpt_is_not_product_marketing(self):
        self.assertEqual(usage_excerpt([{'text': 'Универсальное средство с приятным запахом.', 'verification_status': 'VERIFIED'}]), '')

    def test_regeneration_rotates_with_all_steps_preserved(self):
        answers = {rule_reply(self.review, self.product, revision=i, facts=self.facts) for i in range(3)}
        self.assertEqual(len(answers), 3)
        self.assertTrue(all('30 секунд' in answer for answer in answers))

    def test_quality_recognizes_idiom_but_rejects_unrelated_reply(self):
        # Isolate the pure quality helper without importing production DB dependencies.
        app_path = Path(importlib.util.find_spec('app').submodule_search_locations[0])
        tree = ast.parse((app_path / 'services.py').read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_addresses_review_topic')
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<quality helper>', 'exec'), namespace)
        matches = namespace['_addresses_review_topic']
        self.assertTrue(matches('Деньги на ветер', 'Извините за результат очистки. По инструкции распылите средство.'))
        self.assertFalse(matches('Деньги на ветер', 'Спасибо за покупку! Приятных поездок!'))
        self.assertTrue(matches('Заказал свечи производитель NGK Япония, пришли х/з', 'Извините за разочарование. Для проверки приложите фотографии маркировки.'))
        self.assertFalse(matches('Заказал свечи производитель NGK Япония, пришли х/з', 'Спасибо за покупку! Приятных поездок!'))


if __name__ == '__main__':
    unittest.main()
