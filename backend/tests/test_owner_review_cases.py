"""End-to-end reply regression examples supplied by the owner, with no live AI or web calls."""
import asyncio
import unittest
from unittest.mock import patch, AsyncMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import Base, Product, Review, Draft, StoreProfile, set_setting
from app.services import generate_draft
from app.safety import validate_reply
from app.verified_instructions import matching_instructions
from urllib.parse import urlsplit


CASES = [
 {'name':'Пенный очиститель Profoam 4000','brand':'KANGAROO','rating':5,'text':'','contains':['Спасибо'],'excludes':['Распылите','инструкц','извин']},
 {'name':'Очиститель универсальный Profoam 2000, сменный блок','brand':'KANGAROO','rating':1,'text':'Плохое качество','facts':[{'text':'Способ применения: Нанесите средство на загрязнение. Через 30 секунд протрите влажной тканью.','source':'Карточка товара','verification_status':'VERIFIED'}],'contains':['30 секунд','тканью'],'excludes':['по отзыву нельзя']},
 {'name':'Очиститель интерьера Profoam 4000+, 600мл','brand':'KANGAROO','article':'321499','rating':2,'text':'Деньги на ветер','contains':['10-15 минут'],'excludes':['30 секунд','вернём деньги']},
 {'name':'Ароматизатор Air Spencer A9','brand':'EIKOSHA','rating':2,'text':'Очень слабый запах','contains':['дефлектор','держател'],'excludes':['возврат']},
 {'name':'Ароматизатор Air Spencer A9','brand':'EIKOSHA','rating':2,'text':'Запах не понравился','contains':['индивидуально','другую композицию'],'excludes':['возврат']},
 {'name':'Свеча зажигания BP6HS для Citroen','brand':'NGK','rating':3,'text':'Заказал свечи зажигания производитель нжк япония, пришли х/з','contains':['ПВЗ','отказаться'],'excludes':['Ваши сомнения','производитель полученного товара был понятен']},
 {'name':'Эко средство для мытья полов 1л, аромат кедра','brand':'KANGAROO','rating':2,'text':'Крышка без заводской защиты. Одета криво, часть протекла, запах кислый, липкая (возможно подменили)','contains':['протечку','запах','крышки','ПВЗ'],'excludes':['поддержк','подмен','провер','фотограф']},
 {'name':'Дымовая шашка нейтрализатор запаха с серебром 40 мл','brand':'STEAM DEO','article':'D38RU','rating':1,'text':'Все сделала по инструкции, банка нагрелась, но дым не пошел','contains':['температур','влажност','PUSH'],'excludes':['просроч','повторно запустите']},
 {'name':'Дымовая шашка нейтрализатор запаха с серебром 40 мл','brand':'STEAM DEO','article':'D38RU','rating':1,'text':'Даже и минуты не дымила. Никакого эффекта. Деньги на ветер.','contains':['Если запах остался','повторно не запускайте'],'excludes':['гарантируем']},
 {'name':'Средство для мытья','brand':'OTHER','rating':5,'text':'Один потёк по дороге, упаковали плохо. Сам гель хороший. Рекомендую!','contains':['протечку','понравилось'],'excludes':['просроч']},
 {'name':'Универсальный товар','brand':'OTHER','rating':1,'text':'Верните деньги, товар не работает','contains':['поддержк'],'excludes':['сумма','500 рублей','оформить возврат']},
]


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
                    if index==0:db.add(StoreProfile(id='owner',name='Магазин',provider='wb'))
                    facts=case.get('facts',[])
                    product=Product(id='p'+str(index),name=case['name'],brand=case['brand'],part_number=case.get('article',''),facts=facts,category='Бытовая химия' if 'очиститель' in case['name'].lower() else '')
                    for instruction in matching_instructions(product):
                        product.facts=product.facts+[{'text':instruction['text'],'source':instruction['url'],'verification_status':'VERIFIED','source_type':'manufacturer','kind':'instruction'}]
                        set_setting(db,'official_domain:'+product.id,urlsplit(instruction['url']).hostname)
                    db.add(product);db.flush()
                    review=Review(id='r'+str(index),wb_review_id=str(index),product_id=product.id,rating=case['rating'],text=case['text'],risk='NORMAL',wb_account_id='owner')
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
