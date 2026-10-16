"""End-to-end reply regression examples supplied by the owner, with no live AI or web calls."""
import asyncio
import unittest
from unittest.mock import patch, AsyncMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import Base, Product, Review, Draft, StoreProfile, StoreListing, set_setting
from app.services import generate_draft
from app.safety import validate_reply
from app.verified_instructions import matching_instructions
from urllib.parse import urlsplit


CASES = [{"name":"Лампа H1 галогенная 12В 55Вт 3200K","brand":"KOITO","rating":3,"text":"Поставил светят как мои обычные -варта. Где там галоген ..на вид прозрачное стекло","contains":["прозрачная","газовое наполнение","3200","Извините"],"excludes":["Перед заказом","только синее"]},{"name":"Свеча зажигания K16RU11 Toyota","brand":"DENSO","rating":1,"text":"123466890\nБыушные","contains":["только новый","бывшие в употреблении","ПВЗ"],"excludes":["Вы ошибаетесь","неправильно"]},{"name":"Эко средство для мытья полов 1л, энергия океана","brand":"KANGAROO","rating":1,"text":"Чудесный препарат с ароматом чистоты.","contains":["аромат","положительный отзыв","измените","случайно"],"excludes":["Извините","не оправдал","пять звёзд","нанесите"]},{"name":"Лампа H7 галогенная 12В 55Вт 5000K","brand":"AVANTECH","rating":1,"text":"","contains":["почему","оценку","не устроило"],"excludes":["высокую оценку","инструкц"]},{"name":"5W-30 DL-1 Масло моторное синтетическое 1 л Soft99 4 штуки","brand":"GIGAS","rating":1,"text":"3 из 4 бутылок помяты, масло вытекло","contains":["извинения","протечку масла","ПВЗ","отказаться"],"excludes":["оформить возврат"]},{"name":"Колодки тормозные передние ADVICS SN314","brand":"ADVICS","rating":2,"text":"Очень сильно скрипят.","risk":"HIGH","contains":["притирка","дисков","суппорта","сервисе"],"excludes":["это нормально","притрутся","продолжайте ездить","300 км"]},{"name":"Наконечник рулевой тяги 555 SE-3641L Toyota левый","brand":"555","rating":3,"text":"Установил правый наконечник и получилось так что гайка стала ниже шплинта такое впечатление что палец тоньше в диаметре.","contains":["гайки","шплинта","конуса","сервисе","применяемость"],"excludes":["точно","подложите","левая и правая одинаковые"]},
 {'name':'Пенный очиститель Profoam 4000','brand':'KANGAROO','rating':5,'text':'','contains':['Спасибо'],'excludes':['Распылите','инструкц','извин']},
 {'name':'Очиститель универсальный Profoam 2000, сменный блок','brand':'KANGAROO','rating':1,'text':'Плохое качество','facts':[{'text':'Способ применения: Нанесите средство на загрязнение. Через 30 секунд протрите влажной тканью.','source':'Карточка товара','verification_status':'VERIFIED'}],'contains':['30 секунд','тканью'],'excludes':['по отзыву нельзя']},
 {'name':'Очиститель интерьера Profoam 4000+, 600мл','brand':'KANGAROO','article':'321499','rating':2,'text':'Деньги на ветер','contains':['10-15 минут'],'excludes':['30 секунд','вернём деньги']},
 {'name':'Ароматизатор Air Spencer A9','brand':'EIKOSHA','rating':2,'text':'Очень слабый запах','contains':['дефлектор','держател'],'excludes':['возврат']},
 {'name':'Меловой ароматизатор SAMURAI MAN A-37','brand':'EIKOSHA','rating':1,'text':'Перестала пахнуть через неделю','contains':['через неделю','Меловая основа','температуры','движения воздуха','дефлектор','держател'],'excludes':['Уточните','возврат']},
 {'name':'Меловой ароматизатор A-37','brand':'EIKOSHA','rating':2,'text':'Аромат пропал через пять дней','contains':['перестал ощущаться','Меловая основа','дефлектор'],'excludes':['через неделю','Уточните']},
 {'name':'Сменный картридж GIGA V-90','brand':'EIKOSHA','rating':2,'text':'Запах исчез через неделю','contains':['GIGA','корпус'],'excludes':['Меловая основа','разместить баночку']},
 {'name':'Ароматизатор Air Spencer A9','brand':'EIKOSHA','rating':2,'text':'Запах не понравился','contains':['индивидуально','другую композицию'],'excludes':['возврат']},
 {'name':'Свеча зажигания BP6HS для Citroen','brand':'NGK','rating':3,'text':'Заказал свечи зажигания производитель нжк япония, пришли х/з','contains':['ПВЗ','отказаться'],'excludes':['Ваши сомнения','производитель полученного товара был понятен']},
 {'name':'Эко средство для мытья полов 1л, аромат кедра','brand':'KANGAROO','rating':2,'text':'Крышка без заводской защиты. Одета криво, часть протекла, запах кислый, липкая (возможно подменили)','contains':['протечку','запах','крышки','ПВЗ'],'excludes':['поддержк','подмен','провер','фотограф']},
 {'name':'Дымовая шашка нейтрализатор запаха с серебром 40 мл','brand':'STEAM DEO','article':'D38RU','rating':1,'text':'Все сделала по инструкции, банка нагрелась, но дым не пошел','contains':['температур','влажност','PUSH'],'excludes':['просроч','повторно запустите']},
 {'name':'Дымовая шашка нейтрализатор запаха с серебром 40 мл','brand':'STEAM DEO','article':'D38RU','rating':1,'text':'Даже и минуты не дымила. Никакого эффекта. Деньги на ветер.','contains':['Если запах остался','повторно не запускайте'],'excludes':['гарантируем']},
 {'name':'Средство для мытья','brand':'OTHER','rating':5,'text':'Один потёк по дороге, упаковали плохо. Сам гель хороший. Рекомендую!','contains':['протечку','понравилось'],'excludes':['просроч']},
 {'name':'Универсальный товар','brand':'OTHER','rating':1,'text':'Верните деньги, товар не работает','contains':['поддержк'],'excludes':['сумма','500 рублей','оформить возврат']},
]


FOLLOWUP_CASES = [
 {
  "name": "Держатель ароматизатора на дефлектор CANTULE",
  "brand": "EIKOSHA",
  "rating": 3,
  "text": "Сломался крепеж",
  "contains": [
   "крепёж держателя сломался",
   "не оправдал Ваших ожиданий"
  ],
  "excludes": [
   "Уточните",
   "ПВЗ",
   "поток воздуха"
  ]
 },
 {
  "name": "Меловой ароматизатор WHITY MUSK A-43",
  "brand": "EIKOSHA",
  "rating": 3,
  "text": "Запах вкусный,но пахнет совсем чуть-чуть хот открыли уже вторую сторону,покупала где-то неделю назад,запах ненавязчивый приятный",
  "contains": [
   "сам аромат Вам понравился",
   "тонкий",
   "ненавязчивый",
   "дефлекторе",
   "58434122"
  ],
  "excludes": [
   "Уточните",
   "Снимите",
   "откройте вторую"
  ]
 },
 {
  "name": "Меловой ароматизатор MARINE BLUE SQUASH A-106",
  "brand": "EIKOSHA",
  "rating": 1,
  "text": "Прислали держатель для ароматизатора за 962 руб.... Ужас... На минуточку это был подарок...",
  "contains": [
   "вместо ароматизатора",
   "держатель",
   "в подарок",
   "ПВЗ",
   "отказаться"
  ],
  "excludes": [
   "Уточните",
   "выберите держатель"
  ]
 },
 {
  "name": "Лампа автомобильная H27/2 галогенная 12В 27Вт 4000K 2 шт",
  "brand": "KOITO",
  "rating": 3,
  "text": "Свет с желта как простые дешо вые",
  "contains": [
   "4000 K",
   "5000–6000 K",
   "желтоватый",
   "а не яркость",
   "Извините"
  ],
  "excludes": [
   "не касайтесь",
   "это нормально",
   "точно исправна"
  ]
 }
]
CASES += FOLLOWUP_CASES


class OwnerReviewCases(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory=sessionmaker(bind=self.engine,expire_on_commit=False)

    def tearDown(self):self.engine.dispose()

    def test_owner_examples_keep_specific_guidance_and_financial_policy(self):
        for index,case in enumerate(CASES):
            with self.subTest(product=case['name'],review=case['text']):
                with self.factory() as db:
                    if index==0:
                        db.add(StoreProfile(id='owner',name='Магазин',provider='wb'))
                        db.add(Product(id='58434122',name='Держатель на дефлектор CANTULE',brand='EIKOSHA'));db.flush()
                        db.add(StoreListing(store_id='owner',product_id='58434122'))
                    facts=case.get('facts',[])
                    product=Product(id='p'+str(index),name=case['name'],brand=case['brand'],part_number=case.get('article',''),facts=facts,category='Бытовая химия' if 'очиститель' in case['name'].lower() else '')
                    for instruction in matching_instructions(product):
                        product.facts=product.facts+[{'text':instruction['text'],'source':instruction['url'],'verification_status':'VERIFIED','source_type':'manufacturer','kind':'instruction'}]
                        set_setting(db,'official_domain:'+product.id,urlsplit(instruction['url']).hostname)
                    db.add(product);db.flush()
                    review=Review(id='r'+str(index),wb_review_id=str(index),product_id=product.id,rating=case['rating'],text=case['text'],risk=case.get('risk','NORMAL'),wb_account_id='owner')
                    db.add(review);db.commit()
                    with patch('app.services.ollama.reply_variants',new=AsyncMock(side_effect=ValueError('Модель недоступна'))),patch('app.services.ollama.chat',new=AsyncMock(side_effect=ValueError('Модель недоступна'))),patch('app.services.web_research.search_instructions',new=AsyncMock(return_value=[])):
                        result=asyncio.run(generate_draft(db,review.id))
                    answer=result['text'];validate_reply(answer)
                    for word in case['contains']:self.assertIn(word.lower(),answer.lower())
                    for word in case['excludes']:self.assertNotIn(word.lower(),answer.lower())
                    for word in ['мы внимательно учтём','заявку на возврат','денежную компенсацию','просроченность']:
                        self.assertNotIn(word,answer.lower())
                    timing=result['quality']['timing'];self.assertGreaterEqual(timing['total_seconds'],0)
                    self.assertIn(timing['route'],['rules','model','fallback'])

    def test_faded_scent_intent_and_confirmed_store_holder_survive_regeneration(self):
        from app.reply_policy import analyze_customer_message
        from app.safety import review_intent
        for text in ('Перестала пахнуть через неделю','Запах исчез через неделю','Через два дня выветрился аромат','Пахнет слабо'):
            self.assertIn('WEAK_SCENT',analyze_customer_message(text,1)['complaints'])
            self.assertEqual(review_intent(text),'WEAK_SCENT')
        for text in ('Аромат не пропал, пахнет отлично','Не перестала пахнуть','Неприятный запах исчез после уборки'):
            self.assertNotIn('WEAK_SCENT',analyze_customer_message(text,5)['complaints'])
        with self.factory() as db:
            db.add_all([StoreProfile(id='owner',name='Наш магазин',provider='wb'),StoreProfile(id='other',name='Другой',provider='wb'),Product(id='100001',name='Меловой ароматизатор SAMURAI MAN A-37',brand='EIKOSHA'),Product(id='100002',name='Держатель на дефлектор CANTULE',brand='EIKOSHA'),Product(id='100003',name='Держатель на дефлектор чужой',brand='EIKOSHA'),Product(id='100000',name='Ароматизатор на дефлектор MINI AFTER SHOWER',brand='EIKOSHA')]);db.flush()
            db.add_all([StoreListing(store_id='owner',product_id='100000'),StoreListing(store_id='owner',product_id='100002'),StoreListing(store_id='other',product_id='100003'),Review(id='fade-r',wb_review_id='fade-r',product_id='100001',wb_account_id='owner',rating=1,text='Перестала пахнуть через неделю',risk='NORMAL')]);db.commit()
            with patch('app.services.ollama.reply_variants',new=AsyncMock(side_effect=ValueError('offline'))) as model:
                for _ in range(2):
                    draft=asyncio.run(generate_draft(db,'fade-r'))
                    self.assertIn('100002',draft['text']);self.assertNotIn('100003',draft['text']);self.assertNotIn('100000',draft['text'])
                    self.assertIn('Меловая основа',draft['text']);self.assertNotIn('Уточните',draft['text'])
                model.assert_not_called()

    def test_specific_cases_regenerate_without_model_and_keep_boundaries(self):
        from types import SimpleNamespace
        from app.review_cases import specific_review_case
        with self.factory() as db:
            db.add(StoreProfile(id='owner',name='Магазин',provider='wb'));db.flush()
            for index,case in enumerate(CASES[:7]):
                product=Product(id='specific-p'+str(index),name=case['name'],brand=case['brand'])
                db.add(product);db.flush()
                review=Review(id='specific-r'+str(index),wb_review_id='specific-'+str(index),product_id=product.id,wb_account_id='owner',rating=case['rating'],text=case['text'],risk=case.get('risk','NORMAL'))
                db.add(review);db.commit()
                with patch('app.services.ollama.reply_variants',new=AsyncMock(side_effect=ValueError('offline'))) as model:
                    for _ in range(2):
                        answer=asyncio.run(generate_draft(db,review.id,'Улучши ответ'))['text']
                        for word in case['contains']:self.assertIn(word.lower(),answer.lower())
                    model.assert_not_called()
        product=SimpleNamespace(name='Колодки тормозные',category='')
        review=SimpleNamespace(text='Скрипят, плохо тормозят и перегреваются',rating=1,risk='HIGH')
        self.assertIsNone(specific_review_case(review,product))
        product=SimpleNamespace(name='Средство для пола',category='')
        for text in ('Отличный аромат, но протекла упаковка','Не понравился аромат','Чудесный запах, но не моет'):
            review=SimpleNamespace(text=text,rating=1,risk='NORMAL')
            self.assertNotEqual((specific_review_case(review,product) or {}).get('key'),'positive_low_rating')

    def test_followup_cases_keep_context_on_regeneration(self):
        from types import SimpleNamespace
        from app.reply_policy import analyze_customer_message
        from app.review_cases import specific_review_case
        from app.safety import review_intent
        self.assertEqual(review_intent(FOLLOWUP_CASES[1]['text']),'WEAK_SCENT')
        self.assertEqual(analyze_customer_message(FOLLOWUP_CASES[1]['text'],3)['sentiment'],'mixed')
        self.assertNotIn('WEAK_SCENT',analyze_customer_message('Приятный ненавязчивый аромат',5)['complaints'])
        self.assertNotIn('DAMAGED_ITEM',analyze_customer_message('Крепёж не сломался',5)['complaints'])
        with self.factory() as db:
            db.add(StoreProfile(id='owner',name='Магазин',provider='wb'))
            db.add(Product(id='58434122',name='Держатель на дефлектор CANTULE',brand='EIKOSHA'));db.flush()
            db.add(StoreListing(store_id='owner',product_id='58434122'));db.commit()
            for index,case in enumerate(FOLLOWUP_CASES):
                product=Product(id='follow-p'+str(index),name=case['name'],brand=case['brand'])
                db.add(product);db.flush()
                review=Review(id='follow-r'+str(index),wb_review_id='follow-'+str(index),product_id=product.id,wb_account_id='owner',rating=case['rating'],text=case['text'],risk='NORMAL')
                db.add(review);db.commit()
                with patch('app.services.ollama.reply_variants',new=AsyncMock(side_effect=ValueError('offline'))) as model:
                    for _ in range(2):
                        result=asyncio.run(generate_draft(db,review.id,'Улучши ответ'))
                        for word in case['contains']:self.assertIn(word.lower(),result['text'].lower())
                        for word in case['excludes']:self.assertNotIn(word.lower(),result['text'].lower())
                        self.assertTrue(result['quality']['passed'],result['quality']['issues'])
                    model.assert_not_called()

    def test_new_edit_during_model_wait_is_never_overwritten(self):
        with self.factory() as db:
            db.add(StoreProfile(id='owner',name='Магазин',provider='wb'))
            db.add(Product(id='p',name='Неизвестный товар',brand='OTHER'));db.flush()
            db.add(Review(id='r',wb_review_id='1',product_id='p',rating=3,text='Расскажите подробнее',wb_account_id='owner'))
            db.flush();db.add(Draft(id='d',review_id='r',text='Здравствуйте! Исходный ответ.',original='old',revision=1));db.commit()
            async def user_edits_while_waiting(*args,**kwargs):
                with self.factory() as another:
                    item=another.get(Draft,'d');item.text='Здравствуйте! Текст, который сохранил пользователь.';item.revision=2;another.commit()
                raise ValueError('Модель недоступна')
            with patch('app.services.ollama.reply_variants',user_edits_while_waiting),patch('app.services.web_research.search_instructions',new=AsyncMock(return_value=[])):
                with self.assertRaisesRegex(ValueError,'изменён'):asyncio.run(generate_draft(db,'r','Напиши новый вариант ответа'))
            db.expire_all();self.assertEqual(db.get(Draft,'d').revision,2)
            self.assertIn('сохранил пользователь',db.get(Draft,'d').text)


if __name__=='__main__':unittest.main()
