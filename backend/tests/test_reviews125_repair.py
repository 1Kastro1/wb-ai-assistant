import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as S
from unittest.mock import AsyncMock
import pytest
from app.db import Session, Product, Review, Draft, Memory, Job, User, Action
from app.rule_execution import eikosha_case_template, positive_template
from app.owner_reply_policy import enforce_owner_rules, fingerprint
from app.services import assess_reply, review_verification_facts
from app.draft_audit import recheck_step
from app.review_fast_repair import enqueue

RULES = json.loads((Path(__file__).parent/'fixtures/reviews125_rules.json').read_text(encoding='utf-8'))
TEXT = 'нет. это подделка, практически не пахнет. подделка. запаха практически нет. брал до этого в другом месте, вот там запах держался несколько месяцев.'


def example(market='yandex'):
    return S(id='r',rating=1,text=TEXT,risk='NORMAL',manual=False,safety_critical=False,marketplace=market), S(id='p',name='EIKOSHA меловой ароматизатор Spirit Refill GUCINI A-69',brand='EIKOSHA',category='ароматизатор',facts=[])


@pytest.mark.parametrize('market',['wb','ozon','yandex'])
def test_eikosha_exact_reply_checks_locally_but_low_rating_remains_personal(monkeypatch, market):
    from app.integrations import ollama
    from app.owner_reply_rules import owner_business_facts
    mock=AsyncMock(side_effect=AssertionError('Audited exact scenario needs no model'))
    monkeypatch.setattr(ollama,'structured',mock)
    r,p=example(market)
    facts=owner_business_facts(p,RULES)+[{'kind':'vent_holder','text':'Держатель баночки, артикул 1859520','article_label':'артикул Яндекс Маркета 1859520','wb_nm_id':'1859520','source':'Карточка товара','verification_status':'VERIFIED'}]
    answer=eikosha_case_template(r,p,RULES,facts)
    assert answer and 'дистрибьютором EIKOSHA' in answer and '1859520' in answer
    assert answer.count('С уважением ГК Юником.') == 1
    assert assess_reply(r,p,answer,facts)['passed'],assess_reply(r,p,answer,facts)
    saved,q=asyncio.run(enforce_owner_rules(answer,r,p,RULES,facts,lambda t:assess_reply(r,p,t,facts),check_only=True))
    assert saved==answer and q['passed'] and q['verification_method']=='local_review_case'
    mock.assert_not_awaited()
    assert r.rating==1


@pytest.mark.parametrize('change',['question','safety','giga','money','unknown_rule','extra_complaint'])
def test_eikosha_fast_check_refuses_unreviewed_situations(change):
    r,p=example(); rules=list(RULES)
    if change=='question':r.marketplace='yandex_question'
    elif change=='safety':r.text+=' Началась аллергия.'
    elif change=='giga':p.name='EIKOSHA GIGA V-1 стержни'
    elif change=='money':r.text+=' Как вернуть деньги?'
    elif change=='unknown_rule':rules.append('Всегда назови код партии.')
    else:r.text+=' Пришла разбитая банка.'
    assert eikosha_case_template(r,p,rules,[]) is None


def seed(market='yandex'):
    with Session() as db:
        db.add_all([Memory(id=str(i),kind='REVIEW_RULE',text=t) for i,t in enumerate(RULES)])
        db.add(Product(id='p',name='EIKOSHA меловой ароматизатор A-69',brand='EIKOSHA',category='ароматизатор'))
        db.flush()
        for rid,rating,text in [('positive',5,'Все супер!'),('negative',1,TEXT),('employee',5,'Все супер!'),('other',5,'Неизвестная сложная история'),('published',5,'Все супер!')]:
            db.add(Review(id=rid,wb_review_id=rid,wb_account_id='owner',product_id='p',rating=rating,text=text,marketplace=market,
                is_answered=rid=='published',status='manual_review'))
            db.flush()
            db.add(Draft(id=rid,review_id=rid,text='Старый ответ',original='Старый ответ',revision=3,
                edits=[{'by':'owner'}] if rid=='employee' else [],quality={'passed':False,'text_issues':[],
                    'owner_rules':{'passed':False,'checked':False,'technical_error':True,'issues':['таймаут']}}))
        db.commit()


@pytest.mark.parametrize('market',['wb','ozon','yandex'])
def test_fast_repair_resumes_preserves_edits_and_published_and_never_calls_model(client, monkeypatch, market):
    seed(market)
    from app.integrations import ollama
    mock=AsyncMock(side_effect=AssertionError('Bulk fast repair must not use model'))
    monkeypatch.setattr(ollama,'structured',mock)
    with Session() as db:
        ids=enqueue(db,'owner'); assert enqueue(db,'owner')==ids
    for _ in range(3):asyncio.run(recheck_step(ids[0]))
    with Session() as db:
        job=db.get(Job,ids[0]); assert job.status=='completed'
        assert job.result['checked']==4 and job.result['verified']==2
        assert db.get(Draft,'positive').quality['passed']
        assert db.get(Review,'positive').status=='draft_ready'
        assert db.get(Draft,'negative').quality['passed']
        assert db.get(Review,'negative').status=='manual_review'
        for rid in ('employee','other','published'):
            assert db.get(Draft,rid).text=='Старый ответ' and db.get(Draft,rid).revision==3
        assert not db.query(Action).count()
        assert not db.get(Review,'positive').is_answered
    mock.assert_not_awaited()


def test_pending_check_and_stale_manual_status_do_not_count_as_personal_but_defects_do(client):
    seed()
    with Session() as db:
        for rid in ('positive','employee','other'):
            d=db.get(Draft,rid)
            d.quality={'passed':rid=='positive','text_issues':[],'owner_rules':{'status':'pending','checked':False,'violations':[]}}
        db.commit()
    rows=client.get('/reviews/page',params={'manual_only':True}).json()
    assert {r['id'] for r in rows['items']}=={'negative'}
    assert client.get('/work-queue?view=manual').json()['total']==1
    with Session() as db:
        d=db.get(Draft,'other'); d.quality={**d.quality,'text_issues':['Не соответствует отзыву']}; db.commit()
    assert client.get('/reviews/page',params={'manual_only':True}).json()['total']==2


def test_fast_repair_does_not_restart_cancelled_work(client):
    seed()
    with Session() as db:
        jid=enqueue(db,'owner')[0]
        db.get(Job,jid).cancel_requested=True
        db.commit()
        assert enqueue(db,'owner')==[jid]
    asyncio.run(recheck_step(jid))
    with Session() as db:
        assert db.get(Job,jid).status=='cancelled'
        assert db.get(Draft,'positive').revision==3


def test_recheck_receives_owner_and_catalog_holder_evidence_and_rejects_changed_facts(client, monkeypatch):
    seed()
    from app import services,owner_reply_policy
    facts=[{'kind':'vent_holder','text':'Артикул 1859520','source':'Карточка товара','verification_status':'VERIFIED'}]
    monkeypatch.setattr(services,'review_vent_holder',lambda *args:facts[0])
    async def check(answer,r,p,rules,received,assess,**kwargs):
        assert any(f.get('policy')=='eikosha_distributor' for f in received)
        assert any('1859520' in f['text'] for f in received)
        facts[0]={**facts[0],'text':'Артикул 111'}
        return answer,{'passed':True,'checked':True,'issues':[]}
    monkeypatch.setattr(owner_reply_policy,'enforce_owner_rules',check)
    with Session() as db:
        db.add(Job(id='check',kind='draft_recheck',result={'draft_ids':['negative'],'check_only':True}));db.commit()
    asyncio.run(recheck_step('check'))
    with Session() as db: assert not db.get(Draft,'negative').quality['passed']
