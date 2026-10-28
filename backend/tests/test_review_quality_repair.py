import asyncio
from collections import Counter
from types import SimpleNamespace as S
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select
from app.db import Session, Product, Review, Draft, Job, Memory
from app.reply_policy import analyze_customer_message, semantic_reply_issues
from app.product_guidance import is_cleaner, usage_excerpt
from app.safety import rule_reply
from app.services import assess_reply, propose_publish
from app.rule_execution import positive_body, category_evidence
from app.draft_audit import repair_step, recheck_step


@pytest.mark.parametrize('text,rating,name,old', [
    ('слабоват, через день уже мутные покрышки', 4, 'Чернитель шин', 'Рады, что покупка Вам понравилась.'),
    ('Ни разу еще не попался неприятный запах. Каждый раз шедевр.', 5, 'Ароматизатор', 'Нам жаль, что запах Вам не подошёл.'),
    ('Светят хорошо, жёлтый свет, хорошие лампы!', 5, 'Галогенная лампа', 'Извините, что оттенок света не оправдал ожиданий.'),
    ('запаха нет, нейтральное. Рекомендую', 5, 'Жидкое мыло', 'Рады, что аромат Вам понравился.'),
    ('товар хороший, только коробочки помяты', 4, 'Фильтр', 'Извините за повреждение полученного товара.'),
    ('издают много звуков, плохо чистят', 2, 'Щётка стеклоочистителя', 'Сверьте дозировку и время выдержки разных составов.'),
])
def test_real_contradictions_are_rejected_even_when_courteous(text, rating, name, old):
    r=S(text=text, rating=rating, risk='NORMAL')
    p=S(name=name, category='', brand='', facts=[])
    reply='Добрый день! Спасибо за Ваш отзыв. '+old+' Благодарим за выбор нашего магазина.'
    assert semantic_reply_issues(r,p,reply)
    assert not assess_reply(r,p,reply)['passed']


@pytest.mark.parametrize('name', ['Щетка стеклоочистителя бескаркасная Double Force', 'Держатель ароматизатора', 'Дворники'])
def test_wipers_and_holders_never_receive_chemical_dosing(name):
    p=S(name=name,category='',brand='',facts=[])
    assert not is_cleaner(p)
    if 'стеклоочистител' in name.lower() or 'дворник' in name.lower():
        assert category_evidence(S(text=''),p)=={'parts', 'wipers'}
    r=S(text='Очень сильно скрипит, плохо чистит',rating=2,risk='NORMAL')
    reply=rule_reply(r,p) or ''
    assert not any(x in reply for x in ('дозировк','выдержки','разных составов'))


def test_plain_positive_and_resolved_feedback_keep_their_meaning():
    p=S(name='Лампа галогенная Koito',category='',brand='Koito',facts=[])
    r=S(text='Светят хорошо, жёлтый свет, хорошие лампы!',rating=5,risk='NORMAL')
    assert not analyze_customer_message(r.text,r.rating)['complaints']
    assert 'Извините' not in rule_reply(r,p)
    r.text='Не подошёл, перезаказала другой';p.name='Фильтр'
    answer=rule_reply(r,p)
    assert 'не подош' in answer.lower()
    assert not any(word in answer.lower() for word in ('фото','уточните','через полученный заказ'))


def test_rotation_varies_across_new_reviews_and_is_reproducible():
    counts=Counter(positive_body(S(id=str(i))) for i in range(300))
    assert len(counts)==3 and min(counts.values())>60
    assert positive_body(S(id='x'))==positive_body(S(id='x'))
    assert positive_body(S(id='x'))!=positive_body(S(id='x'),1)


def test_marketing_mislabeled_as_instruction_does_not_become_advice():
    assert usage_excerpt([{'kind':'instruction','verification_status':'VERIFIED','text':'Мы используем передовые технологии, не нанося вреда природе.'}])==''
    assert '3–5 минут' in usage_excerpt([{'kind':'instruction','verification_status':'VERIFIED','text':'Нанесите средство. Подождите 3–5 минут. Смойте водой.'}])


@pytest.mark.parametrize('text', ['Не скрипит, чистит хорошо', 'Не воняет, неприятного запаха нет', 'Коробка не помята, всё отлично', 'Неплохо чистит'])
def test_denied_faults_do_not_create_complaints(text):
    assert not analyze_customer_message(text,5)['complaints']


def test_faded_scent_and_led_failure_are_answered_without_generic_reasking():
    r=S(text='Запах выветривается буквально через неделю',rating=2,risk='NORMAL')
    p=S(name='Ароматизатор GIGA',brand='EIKOSHA',category='',facts=[])
    assert 'WEAK_SCENT' in analyze_customer_message(r.text,2)['complaints']
    assert 'через неделю' in rule_reply(r,p)
    r.text='Не светят';p.name='Светодиодные LED лампы';p.brand='TEST'
    answer=rule_reply(r,p)
    assert 'свет' in answer and 'сервисе' in answer
    assert 'уточните' not in answer.lower()


def test_rule_parse_cache_is_isolated_and_rule_edits_take_effect_immediately():
    from app.owner_reply_policy import constraints, local_issues
    rules=['Не писать «проверено».']
    first=constraints(rules)
    first['banned'].clear()
    assert local_issues('проверено',rules)
    rules[0]='Не писать «готово». Не просить фотографии товара.'
    assert not local_issues('проверено',rules)
    assert local_issues('готово',rules)
    assert local_issues('Пришлите фотографии товара.',rules)


def seed_audit():
    with Session() as db:
        db.add(Memory(id='style',text='Каждый ответ начинай с «Добрый день!». В конце ответа добавляй подпись «С уважением ГК Юником.».'))
        db.add(Product(id='p',name='Щетка стеклоочистителя',brand='TEST',category='Автодетали'))
        db.flush()
        for i in range(3):
            db.add(Review(id=f'r{i}',wb_review_id=f'r{i}',wb_account_id='shop',marketplace='yandex',product_id='p',rating=5 if i<2 else 2,text='' if i<2 else 'Очень сильно скрипит'))
        db.flush()
        for i in range(3):
            old='Добрый день! Спасибо за оценку. '+str(i)
            db.add(Draft(id=f'd{i}',review_id=f'r{i}',text=old,original=old,edits=[{'by':'employee','old':'x','new':old}] if i==1 else [],quality={}))
        db.add(Job(id='audit',kind='draft_audit',status='running',result={'account_id':'shop'}));db.commit()


def test_audit_resumes_preserves_manual_edits_and_marks_unknown_rules_pending(monkeypatch):
    from app.integrations import ollama
    model=AsyncMock(side_effect=AssertionError('quick audit must not call model'))
    monkeypatch.setattr(ollama,'structured',model)
    seed_audit()
    asyncio.run(repair_step('audit',limit=1))
    with Session() as db:
        assert db.get(Job,'audit').result['cursor']=='d0'
        assert db.get(Job,'audit').status=='queued'
        assert db.get(Draft,'d0').text.endswith('С уважением ГК Юником.')
    for _ in range(3):asyncio.run(repair_step('audit',limit=1))
    with Session() as db:
        assert db.get(Draft,'d1').text=='Добрый день! Спасибо за оценку. 1'
        assert 'дозиров' not in db.get(Draft,'d2').text
        assert db.get(Job,'audit').status=='completed'
        assert db.get(Job,'audit').result['checked']==3
        assert db.get(Job,'audit').result['manual_preserved']==1
        assert db.get(Draft,'d2').revision==2
    model.assert_not_awaited()


def test_recheck_does_not_overwrite_edit_made_while_model_waits(monkeypatch):
    import app.owner_reply_policy as policy
    seed_audit()
    with Session() as db:
        db.add(Job(id='check',kind='draft_recheck',status='running',result={'account_id':'shop','draft_ids':['d2']}));db.commit()
    async def check(answer,*args,**kwargs):
        with Session() as db:
            d=db.get(Draft,'d2');d.text='Ручная новая редакция';d.revision+=1;db.commit()
        return 'Нельзя сохранить эту устаревшую генерацию',{'passed':True,'issues':[]}
    monkeypatch.setattr(policy,'enforce_owner_rules',check)
    asyncio.run(recheck_step('check'))
    with Session() as db:
        assert db.get(Draft,'d2').text=='Ручная новая редакция'
        assert db.get(Job,'check').status=='completed'
