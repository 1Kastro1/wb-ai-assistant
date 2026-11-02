from types import SimpleNamespace as NS
from app.reply_policy import analyze_customer_message, authenticity_concern, contextual_complaint_reply
from app.product_guidance import product_usage_reply, positive_review_reply
from app.owner_reply_rules import owner_business_facts

def test_giga_fast_fade_is_not_misplaced_can():
    p = NS(name='Запасной элемент EIKOSHA GIGA SQUASH G-91',brand='EIKOSHA',category='ароматизатор')
    r = NS(text='Оригинальный аромат. Быстро выветривается',rating=4,risk='NORMAL')
    answer=product_usage_reply(r,p,[])
    assert 'не продлевает срок' in answer
    assert 'сильным горячим обдувом' in answer
    assert 'ближе к потоку' not in answer
    assert 'держател' not in answer

def test_minty_filter_is_damage_and_refusal_at_receipt():
    a=analyze_customer_message('Нет. Приехал мятый 👎',1)
    assert 'DAMAGED_ITEM' in a['complaints']
    answer=contextual_complaint_reply(a)
    assert 'отказаться от его получения' in answer
    assert 'Если заказ уже получен' in answer
    assert not analyze_customer_message('Приехал не мятый, всё хорошо',5)['complaints']
    assert 'DAMAGED_ITEM' not in analyze_customer_message('Приехала мятая коробка',2)['complaints']

def test_nitto_comparison_is_not_quality_praise():
    text='Цена. Это вес,он меньше чем у оригинала,290 против 340 оригинал,клапан черного цвета на картинке Оранж.Разница в150-200р.от оригинала,не стоит того.'
    assert authenticity_concern(text)
    p=NS(name='Фильтр масляный Nitto 4M-118',brand='NITTO',category='фильтр')
    r=NS(text=text,rating=4,risk='NORMAL')
    assert positive_review_reply(r,p) is None
    assert product_usage_reply(r,p,[]) is None
    facts=owner_business_facts(p,['Мы продаём оригинальную продукцию NITTO.'])
    answer=product_usage_reply(r,p,facts)
    assert 'оригинальную продукцию NITTO' in answer
    assert 'в весе и цвете клапана' in answer
    assert 'Рады' not in answer
    assert not owner_business_facts(p,['Мы не продаём оригинальную продукцию NITTO.'])
    assert not owner_business_facts(NS(brand='Другой бренд'),['Мы продаём оригинальную продукцию NITTO.'])

def test_positive_original_review_remains_positive():
    assert not authenticity_concern('Оригинал, отличный фильтр, не подделка!')
    assert not authenticity_concern('Расход меньше чем у оригинала, отлично!')

