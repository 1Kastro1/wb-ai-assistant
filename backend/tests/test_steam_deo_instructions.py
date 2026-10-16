import unittest
from types import SimpleNamespace as S
from app.steam_deo_guidance import reviewed_steam_facts
from app.safety import rule_reply, validate_reply


class SteamDeoInstructionTests(unittest.TestCase):
    def setUp(self):
        self.product = S(name='Дымовая шашка нейтрализатор запаха с серебром 40 мл', category='', brand='STEAM DEO', part_number='D38RU', facts=[])
        self.review = S(text='Все сделала по инструкции, банка нагрелась, но дым не пошел', rating=1, risk='NORMAL')

    def test_complete_reply_has_climate_and_instruction(self):
        facts=reviewed_steam_facts(self.product)
        for revision in range(3):
            answer=rule_reply(self.review,self.product,revision,facts)
            for detail in ('Извините','температуры, влажности и освещения','активный состав','Вы уже следовали инструкции','5 минут','10 минут','15 минут','PUSH','не наклоняя','после остывания'):
                self.assertIn(detail,answer)
            self.assertLessEqual(len(answer),900)
            validate_reply(answer)

    def test_no_effect_is_addressed_and_never_guaranteed(self):
        self.review.text='Даже и минуты не дымила. Никакого эффекта. Деньги на ветер.'
        answer=rule_reply(self.review,self.product,facts=reviewed_steam_facts(self.product))
        self.assertIn('Если запах остался',answer)
        self.assertIn('повторно не запускайте',answer)
        self.assertNotIn('гарантируем',answer)
        self.assertLessEqual(len(answer),900)
        validate_reply(answer)

    def test_source_is_labelled_supplier_and_only_exact_model(self):
        self.assertTrue(all(f['source_type']=='supplier' for f in reviewed_steam_facts(self.product)))
        self.product.part_number='D269'
        self.assertFalse(reviewed_steam_facts(self.product))
        self.product.part_number='D38RU'
        self.product.brand='OTHER'
        self.assertFalse(reviewed_steam_facts(self.product))

    def test_positive_review_does_not_get_failed_use_advice(self):
        self.review.text,self.review.rating='Запах исчез, всё понравилось',5
        self.assertNotIn('PUSH',rule_reply(self.review,self.product) or '')


if __name__=='__main__':
    unittest.main()
