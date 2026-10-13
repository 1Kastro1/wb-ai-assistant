import unittest
from types import SimpleNamespace
from app.verified_instructions import matching_instructions, prefer_manufacturer_instructions
from app.product_guidance import usage_excerpt


class VerifiedInstructionTests(unittest.TestCase):
    def product(self, article='321499', name='Profoam 4000+ 600 мл', brand='KANGAROO'):
        return SimpleNamespace(name=name, brand=brand, part_number='', facts=[{'text': 'Артикул производителя: '+article, 'source': 'Карточка WB: характеристики', 'verification_status': 'VERIFIED'}])

    def test_exact_article_selects_plus_instead_of_classic_foam(self):
        plus = matching_instructions(self.product())[0]
        classic = matching_instructions(self.product('320492','Profoam 4000 780 мл'))[0]
        self.assertIn('10-15 минут', plus['text'])
        self.assertNotIn('10-15 минут', classic['text'])
        self.assertNotEqual(plus['url'], classic['url'])

    def test_unknown_brand_model_and_kit_never_inherit_similar_recipe(self):
        for product in (self.product('321499X'), self.product(brand='OTHER'), self.product(name='Набор Profoam 2000 + 4000+')):
            self.assertEqual(matching_instructions(product), [])

    def test_manufacturer_instruction_overrides_marketplace_time(self):
        manufacturer = {**matching_instructions(self.product())[0], 'kind':'instruction', 'source_type':'manufacturer', 'source':'https://kangaru.ru/product/profoam_4000_600_ml', 'verification_status':'VERIFIED'}
        card = {'text':'Очиститель для ткани. Способ применения: Через 30 секунд промойте водой.', 'source':'Карточка WB: описание', 'verification_status':'VERIFIED'}
        facts = prefer_manufacturer_instructions([card, manufacturer])
        self.assertEqual(facts[1]['text'], 'Очиститель для ткани.')
        self.assertIn('30 секунд', card['text'])  # original remains available in the card history/data
        instruction = usage_excerpt(facts)
        self.assertIn('10-15 минут', instruction)
        self.assertNotIn('30 секунд', instruction)
        self.assertNotIn('промойте водой', instruction)
        self.assertIn('влажной тряпки', instruction)

    def test_safety_restrictions_survive_instruction_extraction(self):
        item = matching_instructions(self.product('250607','Leather Conditioner'))[0]
        facts = [{**item,'kind':'instruction','source_type':'manufacturer','verification_status':'VERIFIED'}]
        self.assertIn('Не наносите кондиционер непосредственно на кожу', usage_excerpt(facts))
        self.assertIn('Не используйте данное средство для замши', usage_excerpt(facts))

    def test_without_manufacturer_card_instructions_remain_available(self):
        facts = [{'text':'Способ применения: Через 30 секунд протрите тканью.', 'verification_status':'VERIFIED'}]
        self.assertEqual(prefer_manufacturer_instructions(facts), facts)
        self.assertIn('30 секунд', usage_excerpt(facts))

    def test_multiple_conflicting_articles_require_manual_matching(self):
        product = self.product()
        product.part_number = '320492'
        self.assertEqual(matching_instructions(product), [])

    def test_can_article_does_not_match_refill_or_r90(self):
        self.assertTrue(matching_instructions(self.product('A-24','Air Spencer A-24', 'EIKOSHA')))
        for article in ('I-24', 'H-211', 'A24SET3', 'A-204'):
            self.assertEqual(matching_instructions(self.product(article, 'EIKOSHA', 'EIKOSHA')), [])


if __name__ == '__main__':
    unittest.main()
