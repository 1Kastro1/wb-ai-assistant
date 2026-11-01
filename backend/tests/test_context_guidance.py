import asyncio
from types import SimpleNamespace as Row
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select
from app import services, context_guidance_repair as repair
from app.db import Session, Product, Review, Draft, Memory, Job, Publication, Action, User
from app.reply_policy import analyze_customer_message, semantic_reply_issues
from app.safety import rule_reply, safe_fallback_reply
from app.integrations import ollama

GENERIC = 'Добрый день! Спасибо за отзыв. Извините, что покупка не оправдала Ваших ожиданий. С уважением ГК Юником.'
FUSSO = Row(name='Полироль для автомобиля защитный Fusso 12 Months', brand='SOFT99', category='', facts=[])
SCENT = Row(name='Меловой ароматизатор MARINE BLUE SQUASH A-106', brand='EIKOSHA', category='', facts=[])
FACTS = [{'kind':'instruction','verification_status':'VERIFIED','source_type':'manufacturer',
    'text':'Способ применения Fusso Coat 12 Months: Вымойте и высушите кузов. Нанесите тонким равномерным слоем. Дождитесь высыхания 5–20 минут. Располируйте мягкой сухой тканью.'}]


@pytest.mark.parametrize('text,product,expected,forbidden',[
    ('Аромат в машине так себе звучит.....',SCENT,['Если хочется','дефлекторе','держателя'],['слабее, чем']),
    ('Пахнет 1 день, таких денег не стоит',SCENT,['быстро перестал','держател'],['что именно']),
    ('Запах как у туалетного дезодоранта.',SCENT,['не подошёл','индивидуально'],['вскройте','откройте','дефлектор']),
    ('Воск хороший, но не стоит ждать чудо, паутину видно',FUSSO,['паутинка','предварительная полировка','тонким','5–20','располируйте'],['что именно','Способ применения Fusso']),
])
def test_screenshot_and_neighboring_cases_keep_context_without_a_model(text,product,expected,forbidden):
    review=Row(text=text,rating=3,risk='NORMAL')
    for answer in (rule_reply(review,product,facts=FACTS if product is FUSSO else []),safe_fallback_reply(review,product,facts=FACTS if product is FUSSO else [])):
        assert answer and len(answer)<900
        for phrase in expected: assert phrase in answer
        for phrase in forbidden: assert phrase not in answer
        assert not semantic_reply_issues(review,product,answer)
        assert not services.assess_reply(review,product,GENERIC)['passed']


@pytest.mark.parametrize('text',['Запах не как у туалетного дезодоранта, приятный','Пахнет отлично, держится месяц','После обработки паутины не видно, всё хорошо'])
def test_positive_or_negated_observations_are_not_new_complaints(text):
    analysis=analyze_customer_message(text,5)
    assert not set(analysis['complaints']) & {'SCENT_IMPRESSION','SURFACE_MARKS','WEAK_SCENT','ODOR_COMPLAINT'}


def test_fusso_manufacturer_procedure_overrides_legacy_card_and_is_scoped_to_the_model():
    answer=rule_reply(Row(text='Паутину видно',rating=3,risk='NORMAL'),FUSSO,facts=[{'text':'Способ применения: Нанесите на влажную губку.','verification_status':'VERIFIED'}])
    assert 'сухой' in answer and 'тонким' in answer and 'влажную' not in answer
    other=Row(name='Полироль другая',brand='OTHER',category='',facts=[])
    assert 'Fusso' not in safe_fallback_reply(Row(text='Паутину видно',rating=3,risk='NORMAL'),other)
    from app.product_guidance import fusso_instruction
    assert not fusso_instruction(Row(name='Fusso Speed and Barrier',brand='SOFT99'))


def test_generation_uses_local_guidance_and_a_short_independent_rule_deadline(client,monkeypatch):
    writer=AsyncMock(side_effect=AssertionError('No model generation for known advice'))
    checker=AsyncMock(return_value={'compliant':True,'violations':[],'revised_answer':''})
    monkeypatch.setattr(ollama,'reply_variants',writer);monkeypatch.setattr(ollama,'chat',writer)
    monkeypatch.setattr(ollama,'structured',checker)
    with Session() as db:
        db.add(Product(id='scent',name=SCENT.name,brand='EIKOSHA'));db.flush()
        db.add(Review(id='scent-r',wb_review_id='scent-r',product_id='scent',text='Аромат в машине так себе звучит.....',rating=3))
        db.add(Memory(id='semantic',kind='REVIEW_RULE',text='Сохраняй уважительный тон без обещаний.'));db.commit()
        result=asyncio.run(services.generate_draft(db,'scent-r'))
        assert 'дефлекторе' in result['text']
        assert result['quality']['timing']['route']=='rules'
        assert result['quality']['timing']['model_seconds']==0
    writer.assert_not_awaited();assert checker.await_count
    assert all(call.kwargs.get('timeout_seconds')==8 for call in checker.await_args_list)


def test_failed_rule_check_keeps_useful_guidance_and_durable_retry(client,monkeypatch):
    monkeypatch.setattr(ollama,'structured',AsyncMock(side_effect=TimeoutError('checker timeout')))
    with Session() as db:
        db.add(Product(id='timeout-product',name=SCENT.name,brand='EIKOSHA'));db.flush()
        db.add(Review(id='timeout-r',wb_review_id='timeout-r',product_id='timeout-product',text='Аромат в машине так себе звучит.....',rating=3))
        db.add(Memory(id='timeout-rule',kind='REVIEW_RULE',text='Сохраняй уважительный тон без обещаний.'));db.commit()
        result=asyncio.run(services.generate_draft(db,'timeout-r'))
        assert 'дефлекторе' in result['text'] and not result['quality']['passed']
        assert result['quality']['owner_rules']['technical_error']
        assert db.scalar(select(Job).where(Job.kind=='draft_recheck',Job.result['retry_draft_id'].as_string()==result['id']))


def test_background_repair_preserves_manual_edits_published_slots_and_originals(client):
    with Session() as db:
        actor=db.scalar(select(User).where(User.role=='owner'))
        db.add(Product(id='repair-product',name=SCENT.name,brand='EIKOSHA'));db.flush()
        for rid in ('bad','manual','sent'):
            db.add(Review(id=rid,wb_review_id=rid,product_id='repair-product',rating=3,text='Аромат в машине так себе звучит.....',status='manual_review'));db.flush()
            db.add(Draft(id=rid,review_id=rid,text=GENERIC,original=GENERIC,revision=7,edits=[{'by':'employee'}] if rid=='manual' else []))
        db.add(Action(id='published-action',kind='publish',payload={}))
        db.add(Publication(id='sent-slot',review_id='sent',action_id='published-action',status='published'));db.commit()
        jid=repair.enqueue(db,actor.id,'owner').id
        assert repair.enqueue(db,actor.id,'owner').id==jid
    asyncio.run(repair.step(jid))
    with Session() as db:
        bad=db.get(Draft,'bad');assert 'дефлекторе' in bad.text and bad.revision==8 and bad.original==GENERIC
        assert db.get(Review,'bad').status=='manual_review'
        for did in ('manual','sent'):
            assert db.get(Draft,did).text==GENERIC and db.get(Draft,did).revision==7
        assert db.get(Job,jid).result['changed']==1
        assert db.scalar(select(Job).where(Job.result['retry_draft_id'].as_string()=='bad'))
    asyncio.run(repair.step(jid))
    with Session() as db: assert db.get(Draft,'bad').revision==8
