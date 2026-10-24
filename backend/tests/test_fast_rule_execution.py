import asyncio
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import rule_execution as execution
from app.db import Session, Product, Review, Memory, Draft, Action
from app.owner_reply_policy import enforce_owner_rules, saved_rule_quality
from app.services import generate_draft, draft_public, propose_publish

PASS = {'compliant': True, 'violations': [], 'revised_answer': ''}
HELLO = 'Каждый ответ начинай с «Добрый день!». '
SIGNATURE = 'В конце ответа на отзыв добавляй подпись «С уважением ГК Юником.». '
REVIEWED = 'Пиши кратко и доброжелательно, не выдумывай свойства товара.'
PARTS = 'Применяй только к автозапчастям: учитывай ось и сторону установки.'
UNKNOWN = 'Всегда сообщай точный код упаковки из карточки.'
CURRENT_POLICIES = json.loads((Path(__file__).parent/'fixtures/positive_rating_rules.json').read_text(encoding='utf-8'))
NO_DEMANDS = (Path(__file__).parent/'fixtures/positive_no_demands_rule.txt').read_text(encoding='utf-8').strip()


@pytest.fixture
def approved(monkeypatch):
    profiles = dict(execution.PROFILES)
    profiles[execution.digest(REVIEWED)] = {'local_positive': True}
    profiles[execution.digest(PARTS)] = {'family': 'parts', 'local_positive': True}
    monkeypatch.setattr(execution, 'PROFILES', profiles)
    return [HELLO, SIGNATURE, REVIEWED, PARTS]


def example():
    review = SimpleNamespace(id='r', wb_account_id='shop', wb_review_id='remote', product_id='p',
        text='', rating=5, risk='NORMAL', manual=False, safety_critical=False, topics=[], marketplace='ozon', created_at='2026-10-23')
    product = SimpleNamespace(id='p', name='Ароматизатор', brand='EIKOSHA', category='', part_number='A-1', facts=[], marketplace='ozon')
    return review, product


@pytest.mark.parametrize('marketplace', ['wb','ozon','yandex'])
def test_positive_generation_and_rotation_use_no_model_or_publication(client, monkeypatch, approved, marketplace):
    from app.integrations import ollama
    checker = AsyncMock(side_effect=AssertionError('Fast route must not call the model'))
    monkeypatch.setattr(ollama,'structured',checker)
    monkeypatch.setattr(ollama,'reply_variants',checker)
    monkeypatch.setattr(ollama,'chat',checker)
    with Session() as db:
        db.add_all([Memory(id=f'rule-{i}',kind='REVIEW_RULE',text=text) for i,text in enumerate(approved)])
        db.add(Product(id='p',name='Ароматизатор',brand='EIKOSHA',category=''))
        db.flush()
        db.add(Review(id='r',wb_review_id='remote',product_id='p',rating=5,text='',marketplace=marketplace))
        db.commit()
        replies = []
        for _ in range(3):
            result = asyncio.run(generate_draft(db,'r'))
            replies.append(result['text'])
            assert result['quality']['passed'], result['quality']
            assert result['quality']['owner_rules']['verification_method']=='local_positive'
            assert result['text'].startswith('Добрый день!')
            assert result['text'].count('С уважением ГК Юником.')==1
            assert 'Ароматизатор' not in result['text']
            assert draft_public(db,db.get(Draft,result['id']))['quality']['owner_rules']['passed']
        assert len(set(replies))==3
        assert not db.get(Review,'r').is_answered
        assert list(db.query(Action))==[]
        pending = propose_publish(db,['r'])
        assert pending['status']=='pending'
    checker.assert_not_awaited()


@pytest.mark.parametrize('change', ['text','rating','risk','manual','safety_critical','question','unknown_rule','edited_rule'])
def test_fast_path_refuses_nontrivial_or_unknown_policy(approved, change):
    row, product = example()
    rules = list(approved)
    if change=='text':row.text='Слабо пахнет'
    elif change=='rating':row.rating=3
    elif change=='risk':row.risk='HIGH'
    elif change=='manual':row.manual=True
    elif change=='safety_critical':row.safety_critical=True
    elif change=='question':row.marketplace='ozon_question'
    elif change=='unknown_rule':rules.append(UNKNOWN)
    else:rules[-1]+=' Также всегда называй OEM.'
    assert execution.positive_template(row,rules) is None


def test_new_rule_still_requires_semantic_verification(monkeypatch,approved):
    from app.integrations import ollama
    checker=AsyncMock(return_value=dict(PASS))
    monkeypatch.setattr(ollama,'structured',checker)
    row,product=example()
    rules=[*approved, UNKNOWN]
    answer,quality=asyncio.run(enforce_owner_rules('Добрый день! Спасибо за оценку.',row,product,rules,[],lambda _: {'passed':True},allow_fast_positive=True))
    assert quality['passed'] and quality['verification_method']=='model'
    checker.assert_awaited_once()
    assert UNKNOWN in checker.call_args.args[0][-1]['content']


def test_known_conditional_rules_filter_conservatively(approved):
    row,product=example()
    selected,skipped=execution.select_rules([PARTS,UNKNOWN],row,product)
    assert selected==[UNKNOWN] and skipped
    product.name='Неопределённый товар'
    assert execution.select_rules([PARTS],row,product)[0]==[PARTS]
    product.name='Ароматизатор'
    row.text='Нужен фильтр для Toyota'
    assert execution.select_rules([PARTS],row,product)[0]==[PARTS]
    row.text='Подберите по VIN автомобиля'
    assert execution.select_rules([PARTS],row,product)[0]==[PARTS]
    assert execution.select_rules([PARTS+' Учти крепление.'],row,product)[0]==[PARTS+' Учти крепление.']


def test_changed_rules_invalidate_previously_fast_checked_draft(approved):
    row,product=example()
    answer=execution.positive_template(row,approved)
    _,quality=asyncio.run(enforce_owner_rules(answer,row,product,approved,[],lambda _: {'passed':True},allow_fast_positive=True))
    assert saved_rule_quality(answer,approved,quality)['passed']
    assert not saved_rule_quality(answer,[*approved,UNKNOWN],quality)['passed']
    assert not saved_rule_quality(answer+' Другой текст.',approved,quality,changed=True)['passed']


def test_cache_reuses_only_complete_verdict_and_copies_results(monkeypatch):
    from app.integrations import ollama
    checker=AsyncMock(return_value=dict(PASS))
    monkeypatch.setattr(ollama,'structured',checker)
    row,product=example()
    row.text='Хороший аромат'
    async def run():
        return await enforce_owner_rules('Добрый день! Спасибо за отзыв.',row,product,[UNKNOWN],[],lambda _: {'passed':True})
    _,first=asyncio.run(run())
    _,second=asyncio.run(run())
    assert not first['cache_hit'] and second['cache_hit']
    assert second['verification_method']=='exact_input_cache'
    second['issues'].append('Do not mutate the stored verdict')
    assert asyncio.run(run())[1]['issues']==[]
    checker.assert_awaited_once()


@pytest.mark.parametrize('change', ['answer','buyer_text','rating','marketplace','shop','product_name','brand','category','part_number','product_facts','verified_facts','instruction','rules','model'])
def test_every_meaningful_input_invalidates_cache_key(change):
    row,product=example()
    data=dict(answer='Спасибо за оценку.',review=row,product=product,rules=[UNKNOWN],facts=[{'text':'Факт','source':'карточка'}],instruction='',model_profile={'model':'one'})
    before=execution.verification_key(**data)
    if change=='answer':data['answer']+=' Спасибо!'
    elif change=='buyer_text':row.text='Не подошло'
    elif change=='rating':row.rating=1
    elif change=='marketplace':row.marketplace='wb'
    elif change=='shop':row.wb_account_id='other'
    elif change=='product_name':product.name='Другой товар'
    elif change=='brand':product.brand='OTHER'
    elif change=='category':product.category='Фильтр'
    elif change=='part_number':product.part_number='A-2'
    elif change=='product_facts':product.facts=[{'text':'Новые характеристики'}]
    elif change=='verified_facts':data['facts'][0]['text']='Изменённый факт'
    elif change=='instruction':data['instruction']='Добавь инструкцию'
    elif change=='rules':data['rules'].append('Новое правило')
    else:data['model_profile']['model']='two'
    assert execution.verification_key(**data)!=before


@pytest.mark.parametrize('verdict',[{'compliant':False,'violations':['Нарушение'],'revised_answer':''}, {'compliant':True}])
def test_failed_or_incomplete_checks_are_never_cached(monkeypatch,verdict):
    from app.integrations import ollama
    checker=AsyncMock(return_value=verdict)
    monkeypatch.setattr(ollama,'structured',checker)
    row,product=example()
    for _ in range(2):
        _,result=asyncio.run(enforce_owner_rules('Спасибо!',row,product,[UNKNOWN],[],lambda _: {'passed':True}))
        assert not result['passed'] and not result['cache_hit']
    assert checker.await_count==2


def test_protected_and_explicit_instruction_do_not_take_positive_shortcut(monkeypatch,approved):
    from app.integrations import ollama
    checker=AsyncMock(return_value=dict(PASS))
    monkeypatch.setattr(ollama,'structured',checker)
    row,product=example()
    answer=execution.positive_template(row,approved)
    _,quality=asyncio.run(enforce_owner_rules(answer,row,product,approved,[],lambda _: {'passed':True},protected=True,allow_fast_positive=True))
    assert not quality['passed']
    checker.assert_not_awaited()
    _,quality=asyncio.run(enforce_owner_rules(answer,row,product,approved,[],lambda _: {'passed':True},allow_fast_positive=True,instruction='Измени стиль'))
    assert quality['verification_method']=='model'
    checker.assert_awaited_once()


def test_cache_is_bounded_and_expires(monkeypatch):
    clock=[1.]
    monkeypatch.setattr(execution,'monotonic',lambda:clock[0])
    good={'checked':True,'passed':True,'issues':[]}
    for i in range(execution.CACHE_LIMIT+1):execution.remember_verdict(str(i),'Спасибо!',good)
    assert execution.cached_verdict('0') is None
    assert execution.cached_verdict(str(execution.CACHE_LIMIT))
    clock[0]+=execution.CACHE_SECONDS+1
    assert execution.cached_verdict(str(execution.CACHE_LIMIT)) is None


@pytest.mark.parametrize('marketplace', ['wb', 'ozon', 'yandex'])
@pytest.mark.parametrize('rating', [4, 5])
def test_current_category_policies_do_not_send_empty_rating_to_model(client, monkeypatch, marketplace, rating):
    from app.integrations import ollama
    checker = AsyncMock(side_effect=AssertionError('Empty positive rating must not wait for Ollama'))
    for method in ('structured', 'reply_variants', 'chat'):
        monkeypatch.setattr(ollama, method, checker)
    with Session() as db:
        # All current global policies, including the 13 newer recommendations.
        rules = [row['text'] for row in CURRENT_POLICIES if row['scope'] == 'Все товары'] + [NO_DEMANDS]
        db.add_all([Memory(id=f'live-{i}',kind='REVIEW_RULE',text=text) for i,text in enumerate(rules)])
        db.add(Product(id='cleaner',name='Универсальный спрей очиститель с ароматом мяты, 500мл',brand='POLLY ROLLY'))
        db.flush()
        db.add(Review(id='rating',wb_review_id='remote-rating',product_id='cleaner',rating=rating,text='',marketplace=marketplace))
        db.commit()
        replies = []
        for _ in range(3):
            result = asyncio.run(generate_draft(db,'rating'))
            policy = result['quality']['owner_rules']
            assert policy['matched'] == 26
            assert policy['passed'] and policy['checked']
            assert policy['verification_method'] == 'local_positive'
            assert result['quality']['timing']['model_seconds'] == 0
            assert result['quality']['timing']['web_seconds'] == 0
            assert result['text'].startswith('Добрый день!')
            assert result['text'].count('С уважением ГК Юником.') == 1
            assert not any(word in result['text'].lower() for word in ('очистит', 'vin', 'возврат', 'довольны', 'совместим'))
            replies.append(result['text'])
        assert len(set(replies)) == 3
        assert db.get(Review,'rating').is_answered is False
        assert list(db.query(Action)) == []
    checker.assert_not_awaited()


def test_audited_recommendation_edits_and_unknown_rules_still_require_model():
    review, product = example()
    rules = [row['text'] for row in CURRENT_POLICIES]
    assert execution.positive_template(review,rules,product=product)
    assert execution.positive_template(review,[*rules,UNKNOWN],product=product) is None
    changed = next(row['text'] for row in CURRENT_POLICIES if row['text'].startswith('Рекомендации для отзывов — Очистители'))
    assert execution.positive_template(review,[*rules,changed+' Всегда обещай результат.'],product=product) is None
    assert execution.positive_template(review,[*rules,NO_DEMANDS],product=product)
    assert execution.positive_template(review,[*rules,NO_DEMANDS+' Всегда указывай страну производства.'],product=product) is None


def test_positive_eligibility_matches_certified_context_exclusions(monkeypatch):
    review, product = example()
    workflow = 'Рабочий процесс: сохраняй черновик, не публикуй самостоятельно.'
    profiles = dict(execution.PROFILES)
    profiles[execution.digest(workflow)] = {'workflow':True}
    monkeypatch.setattr(execution,'PROFILES',profiles)
    rules = [HELLO,SIGNATURE,workflow]
    answer = execution.positive_template(review,rules,product=product)
    assert answer
    _, quality = asyncio.run(enforce_owner_rules(answer,review,product,rules,[],lambda _: {'passed':True},allow_fast_positive=True))
    assert quality['verification_method'] == 'local_positive'
