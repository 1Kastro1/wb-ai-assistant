"""Regression cases found by comparing the whole Yandex draft corpus."""
import re
from types import SimpleNamespace as S
import pytest
from app.safety import rule_reply, safe_fallback_reply
from app.reply_policy import analyze_customer_message, semantic_reply_issues
from app.product_guidance import positive_detail
from app.owner_reply_policy import attach_rule_quality, saved_rule_quality


def pair(text, name='Товар', rating=5, brand='TEST'):
    return S(text=text, rating=rating, risk='NORMAL'), S(name=name, category='', brand=brand, facts=[])


@pytest.mark.parametrize('text', ['вроде как оригинал, установлю и дополню', 'всё как в описании', 'не как все эти лимонные', 'какие могут быть недостатки у резинки?'])
def test_comparisons_do_not_trigger_instructions(text):
    r,p=pair(text, 'Колодки ADVICS')
    p.facts=[{'kind':'instruction','verification_status':'VERIFIED','text':'Проверьте диски. Установите колодки.'}]
    assert not analyze_customer_message(text,5)['is_question']
    assert 'инструкц' not in rule_reply(r,p).lower()


@pytest.mark.parametrize('text', ['Как установить колодки?', 'Подскажите способ применения', 'Можно ли разводить водой?', 'Сколько нужно средства?'])
def test_real_requests_remain_questions(text):
    assert analyze_customer_message(text,5)['is_question']


def test_mixed_scent_does_not_claim_buyer_liked_it():
    r,p=pair('долго держится, аромат мне не особо но жена оценила', 'Ароматизатор Eikosha', brand='EIKOSHA')
    assert 'ODOR_COMPLAINT' in analyze_customer_message(r.text,5)['complaints']
    answer=rule_reply(r,p)
    assert 'не вполне Вам подошёл' in answer
    assert 'покупка Вам понравилась' not in answer


def test_price_and_partial_scent_are_not_fabricated_praise():
    r,p=pair('Запахи приятные 2 из 3\nЦена', 'Набор ароматизаторов',4)
    old='Рады, что аромат Вам понравился и Вас устроила цена покупки.'
    assert semantic_reply_issues(r,p,old)
    assert 'Вас устроила цена' not in rule_reply(r,p)


def test_inexpensive_is_not_misread_as_expensive():
    assert 'Вас устроила цена' in positive_detail('недорогой, хороший товар')
    assert 'Вас устроила цена' not in positive_detail('хороший, но дорогой')


def test_short_size_is_not_a_complaint_in_positive_context():
    assert 'SIZE_ISSUE' not in analyze_customer_message('короткая и удобная, отличная щётка',5)['complaints']


def test_subjects_are_retained_in_positive_details():
    answer=positive_detail('Добротно сделан, установился плотно')
    assert 'и оценили' not in answer
    assert 'Вы оценили исполнение' in answer
    r,p=pair('запаха нет, нейтральное. Рекомендую','Жидкое мыло')
    answer=rule_reply(r,p)
    assert len(re.findall('Спасибо|Благодарим',answer))==1


@pytest.mark.parametrize('text, topic', [('короткая','длина'),('не подошло','подош'),('товар хороший, только коробочки помяты','упаков')])
def test_specific_complaints_survive_fallback(text,topic):
    r,p=pair(text,'Фильтр',2)
    assert topic in safe_fallback_reply(r,p).lower()
    if 'коробочки' in text:
        assert 'повреждение полученного товара' not in safe_fallback_reply(r,p)


def test_resolved_return_is_acknowledged_without_a_new_request():
    r,p=pair('не подходит к Outlander. Магазин одобрил возврат, за это пять звёзд','Колодки')
    answer=rule_reply(r,p)
    assert 'уже решён' in answer
    assert not re.search('фотограф|уточните|ПВЗ',answer)


@pytest.mark.parametrize('name', ['Набор R90 Gucini+R90 Samurai Neo','Ароматизатор неизвестной серии'])
def test_unknown_fragrance_is_never_given_giga_instructions(name):
    r,p=pair('Запах ощущается только если поднести нос',name,1,'EIKOSHA')
    answer=rule_reply(r,p) or safe_fallback_reply(r,p)
    assert 'GIGA' not in answer
    assert 'аромат' in answer


def test_timeout_is_distinct_from_a_violation_and_never_an_approval():
    q=attach_rule_quality({'passed':True,'issues':[]},{'passed':False,'checked':False,'technical_error':True,'issues':['таймаут']})
    assert q['owner_rules']['status']=='error'
    assert q['text_issues']==[] and not q['passed']
    q=attach_rule_quality({'passed':False,'issues':['не учтена упаковка']},{'passed':False,'checked':True,'issues':['нет подписи']})
    assert q['text_issues']==['не учтена упаковка']
    assert q['owner_rules']['status']=='violation'


def test_changed_text_cannot_inherit_previous_timeout_or_approval():
    r,p=pair('отлично')
    q=saved_rule_quality('Новый ответ',['Учитывай пожелания покупателя.'],{'passed':True,'technical_error':True,'reason':'таймаут'},changed=True,review=r)
    assert not q['passed'] and not q['checked']
    assert not q.get('technical_error') and not q.get('reason')
