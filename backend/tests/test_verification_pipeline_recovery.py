import asyncio
from types import SimpleNamespace as NS
import pytest
from sqlalchemy import select
from app.db import Session, Job, Review, Product, GenerationOutcome
from app import review_regeneration as regeneration, services
from app.generation_control import FastPathUnavailable
from app.rule_execution import verified_positive_reply, simple_positive
from test_saved_positive_publication import RULES
from test_technical_rule_retry import seed_error


@pytest.mark.parametrize('market', ['wb','ozon','yandex'])
def test_composed_praise_and_no_claims_reply_pass_without_model(market):
    from app.owner_reply_policy import enforce_owner_rules
    r=NS(id='praise',text='Все отлично! Спасибо, рекомендую.',rating=5,
        risk='NORMAL',manual=False,safety_critical=False,marketplace=market)
    p=NS(name='Товар',category='',brand='TEST')
    answer='Здравствуйте. Благодарим за положительный отзыв! Нам приятно, что Вы остались довольны покупкой. Желаем Вам хорошего дня.\nС уважением ГК Юником.'
    assert simple_positive(r) and verified_positive_reply(answer,r,RULES,product=p)
    text,verdict=asyncio.run(enforce_owner_rules(answer,r,p,RULES,[],lambda v:{'passed':True},check_only=True,local_only=True))
    assert text==answer and verdict['passed'] and verdict['verification_method']=='local_saved_positive'


@pytest.mark.parametrize('message', ['Все отлично, но не работает','Все отлично. Как установить?',
    'Спасибо, протекает','Все отлично, верните деньги','Отлично! не рекомендую'])
def test_unknown_or_mixed_clauses_do_not_get_fast_approval(message):
    r=NS(text=message,rating=5,risk='NORMAL',manual=False,safety_critical=False,marketplace='wb')
    assert not simple_positive(r)


@pytest.mark.parametrize('extra', [' Мы гарантируем результат.', ' Товар оригинальный.',
    ' Служит 10 лет.', ' Обратитесь в поддержку.', ' Спасибо за отзыв.'])
def test_reply_grammar_rejects_claims_advice_and_repeated_thanks(extra):
    r=NS(text='Все отлично',rating=5,risk='NORMAL',manual=False,safety_critical=False,marketplace='wb')
    answer='Здравствуйте. Спасибо за отзыв.'+extra+'\nС уважением ГК Юником.'
    assert not verified_positive_reply(answer,r,RULES)
    assert not verified_positive_reply('Здравствуйте. Спасибо за отзыв.\nС уважением ГК Юником.',r,[*RULES,'Обещай подарок.'])


def test_fast_failure_defers_one_review_and_survives_restart(client,monkeypatch):
    with Session() as db:
        db.add(Product(id='p',name='Товар'));db.flush()
        for rid,day in [('bad','03'),('good','02')]:
            db.add(Review(id=rid,wb_review_id=rid,product_id='p',rating=5,text='',created_at='2026-11-'+day,marketplace='wb'))
        db.add(Job(id='regen',kind='draft_recheck',result={'policy_regeneration':True,'account_id':'owner','cutoff':'2026-11-10','total':2}));db.commit()
    calls=[]
    async def generate(db,rid,**kwargs):
        calls.append((rid,kwargs['fast_only']))
        if rid=='bad' and kwargs['fast_only']:raise FastPathUnavailable()
        db.merge(GenerationOutcome(job_id='regen',review_id=rid,category='generated'));db.commit()
    monkeypatch.setattr(services,'generate_draft',generate)
    asyncio.run(regeneration.step('regen'))
    with Session() as db:
        j=db.get(Job,'regen')
        assert not j.result.get('fast_disabled') and j.result['processed']==1, (j.result,calls)
        assert db.get(GenerationOutcome,('regen','bad')).category=='deferred_fast'
    asyncio.run(regeneration.step('regen'))
    assert calls==[('bad',True),('good',True),('bad',False)]
    with Session() as db:
        assert db.get(Job,'regen').status=='completed'
        assert db.get(Job,'regen').result['processed']==2


def test_deferred_generation_records_completion_and_usage_once(client):
    from app.generation_control import batch_context, record_outcome
    from app.db import Draft
    seed_error()
    with Session() as db:
        db.add(Job(id='regen',kind='draft_recheck',result={}))
        db.flush();db.add(GenerationOutcome(job_id='regen',review_id='r',category='deferred_fast'));db.commit()
        token=batch_context.set({'job_id':'regen'})
        try:
            record_outcome(db,db.get(Review,'r'),db.get(Draft,'d'));db.commit()
            record_outcome(db,db.get(Review,'r'),db.get(Draft,'d'));db.commit()
        finally:batch_context.reset(token)
        assert db.get(GenerationOutcome,('regen','r')).category=='generated'
        assert sum(v for k,v in db.get(Job,'regen').result['usage'].items() if k in ('model','local','template'))==1


@pytest.mark.parametrize('passed',[False,True])
def test_overlapping_scans_share_result_backoff_but_manual_click_retries(client,monkeypatch,passed):
    from app.draft_audit import recheck_step
    from app.owner_reply_policy import fingerprint
    from app.owner_reply_rules import matching_owner_rules
    seed_error()
    calls=[]
    monkeypatch.setattr(services,'assess_reply',lambda *a,**k:{'passed':True,'issues':[]})
    async def check(answer,review,product,rules,*a,**k):
        calls.append(review.id)
        return answer,{'fingerprint':fingerprint(rules),'passed':passed,'checked':passed,
            'technical_error':not passed,'issues':[] if passed else ['timeout']}
    monkeypatch.setattr('app.owner_reply_policy.enforce_owner_rules',check)
    with Session() as db:
        for jid in ('a','b','manual','changed'):
            db.add(Job(id=jid,kind='draft_recheck',result={'account_id':'owner','draft_ids':['d'],
                'check_only':True,'manual_check':jid=='manual'}))
        db.commit()
    asyncio.run(recheck_step('a'));asyncio.run(recheck_step('b'))
    assert calls==['r']
    asyncio.run(recheck_step('manual'))
    # A successful exact-context library check can also satisfy the manual
    # request; an outage must get a new attempt even during its backoff.
    assert calls==['r'] if passed else calls==['r','r']
    with Session() as db:
        db.get(Review,'r').text='Другой отзыв';db.commit()
    asyncio.run(recheck_step('changed'))
    assert calls==['r','r'] if passed else calls==['r','r','r']


def test_legacy_disabled_fast_lane_resumes_without_regenerating_outcomes(client,monkeypatch):
    with Session() as db:
        db.add(Product(id='p',name='Товар'));db.flush()
        for rid in ('done','new'):
            db.add(Review(id=rid,wb_review_id=rid,product_id='p',rating=5,text='',created_at='2026-11-01',marketplace='wb'))
        db.add(Job(id='legacy',kind='draft_recheck',result={'account_id':'owner','cutoff':'2026-11-10',
            'total':2,'fast_disabled':True,'fast_done':True,'context_done':True}));db.flush()
        db.add(GenerationOutcome(job_id='legacy',review_id='done',category='generated'));db.commit()
    calls=[]
    async def generate(db,rid,**kwargs):
        calls.append(rid);db.merge(GenerationOutcome(job_id='legacy',review_id=rid,category='generated'));db.commit()
    monkeypatch.setattr(services,'generate_draft',generate)
    asyncio.run(regeneration.step('legacy'))
    assert calls==['new']
    with Session() as db:
        assert db.get(Job,'legacy').status=='completed'
        assert db.get(Job,'legacy').result['processed']==2
