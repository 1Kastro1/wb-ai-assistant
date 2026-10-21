import unittest
from types import SimpleNamespace
from app.plain_text import plain_text
from app.safety import validate_reply, rule_reply
from app.product_guidance import usage_excerpt
from app.reply_policy import analyze_customer_message


class ReviewPlainTextTests(unittest.TestCase):
    def test_nested_encoded_html_becomes_readable_text(self):
        self.assertEqual(plain_text('Добрый&nbsp;день!&lt;br/&gt;&amp;lt;br/&amp;gt;<b>Совет</b>'), 'Добрый день!\nСовет')
        self.assertEqual(plain_text('<p>Первый</p><p>Второй</p><script>secret()</script><style>.x{}</style>'), 'Первый\nВторой')
        self.assertEqual(plain_text('1 < 2; 3 > 2'), '1 < 2; 3 > 2')

    def test_final_validation_cleans_markup_before_checking_safety(self):
        self.assertEqual(validate_reply('Добрый день!<br/>Спасибо за отзыв.'), 'Добрый день!\nСпасибо за отзыв.')
        with self.assertRaises(ValueError):
            validate_reply('<b>гарантируем</b> результат')
        with self.assertRaises(ValueError):
            validate_reply('<br/><div></div>')

    def test_usage_does_not_dump_html_precautions_or_volume(self):
        facts = [
            {'text':'Форма выпуска: жидкость\nОсобенности: Не требует смывания водой\nОбъём: 600 мл', 'kind':'feature', 'verification_status':'VERIFIED'},
            {'text':'Особенности: Не требует смывания. Способ применения: Распылите средство.<br/>Оставьте на 30 секунд и промойте водой.<br/><br/>Меры предосторожности:<br/>Не вдыхайте испарения. 600 мл.', 'kind':'feature', 'verification_status':'VERIFIED'},
        ]
        value = usage_excerpt(facts)
        self.assertIn('30 секунд', value)
        for token in ('<', 'Особенности', 'Не требует', '600', 'Меры предосторожности'):
            self.assertNotIn(token, value)

    def test_scent_vocabulary_and_negation(self):
        analysis = analyze_customer_message('Жалкая пародия на Айкошу, пахнет кое-как', 1)
        self.assertIn('AUTHENTICITY', analysis['complaints'])
        self.assertIn('WEAK_SCENT', analysis['complaints'])
        self.assertNotIn('AUTHENTICITY', analyze_customer_message('Не подделка, хороший аромат', 5)['complaints'])

    def test_date_and_wiper_rules_are_specific_and_not_unsafe_guesses(self):
        review = SimpleNamespace(text='Изготовлено 26.07.21. Срок годности 5 лет. Отправили просроченное средство', rating=1, risk='NORMAL')
        product = SimpleNamespace(name='Очиститель Profoam 2000', brand='KANGAROO', category='')
        answer = rule_reply(review, product)
        for token in ('год–месяц–день', 'Если', '21 июля 2026', 'фото'):
            self.assertIn(token, answer)
        for token in ('Распылите', 'годен до', 'точно', 'просрочен'):
            self.assertNotIn(token, answer)
        validate_reply(answer)
        product.brand = 'OTHER'
        self.assertNotIn('год–месяц–день', rule_reply(review, product) or '')
        product.name, product.brand = 'Щетка стеклоочистителя гибридная Double Force', 'Double Force'
        review.text = 'Научили полосить через пару дней'
        answer = rule_reply(review, product)
        for token in ('стекло', 'кромку', 'креплении', 'заменить', 'обзора'):
            self.assertIn(token, answer)
        validate_reply(answer)
        review.text, review.rating = 'Отличная щетка, не полосит, без разводов', 5
        self.assertNotIn('заменить', rule_reply(review, product) or '')


if __name__ == '__main__':
    unittest.main()
